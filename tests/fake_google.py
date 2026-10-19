"""In-memory stand-in for GoogleApi.

It models what matters for schema drift: Developer Metadata is attached to a
column *object*, not an index, so renaming a header, moving a column, renaming
a tab or sorting rows behaves the way it does in real Sheets. Deleting a column
drops its metadata; duplicating a tab copies it.
"""
import asyncio
import copy
import itertools
from dataclasses import dataclass, field
from typing import Any

from services.google.errors import GoogleApiError

_ids = itertools.count(1)


@dataclass
class FakeTab:
    sheet_id: int
    title: str
    columns: list[int] = field(default_factory=list)  # column object ids, in display order
    rows: list[dict[int, Any]] = field(default_factory=list)  # row 0 is the header

    def add_column(self, index: int | None = None) -> int:
        column_id = next(_ids)
        self.columns.insert(len(self.columns) if index is None else index, column_id)
        return column_id

    def column_at(self, index: int) -> int:
        while index >= len(self.columns):
            self.add_column()
        return self.columns[index]

    def set_cell(self, row_index: int, column_index: int, value: Any) -> None:
        while row_index >= len(self.rows):
            self.rows.append({})
        column_id = self.column_at(column_index)
        if value in (None, ""):
            self.rows[row_index].pop(column_id, None)
        else:
            self.rows[row_index][column_id] = value

    def grid(self) -> list[list]:
        return [[row.get(column_id) for column_id in self.columns] for row in self.rows]


@dataclass
class FakeSpreadsheet:
    spreadsheet_id: str
    title: str
    tabs: list[FakeTab]
    # {"key", "value", "kind": spreadsheet|sheet|column, "sheet_id", "column_id"}
    metadata: list[dict] = field(default_factory=list)
    app_properties: dict = field(default_factory=dict)
    trashed: bool = False
    deleted: bool = False
    other_requests: list[dict] = field(default_factory=list)

    def tab(self, sheet_id: int) -> FakeTab:
        for tab in self.tabs:
            if tab.sheet_id == sheet_id:
                return tab
        raise GoogleApiError(400, f"No grid with id: {sheet_id}")


def _cell_value(cell: dict) -> Any:
    entered = cell.get("userEnteredValue") or {}
    for key in ("stringValue", "numberValue", "boolValue"):
        if key in entered:
            return entered[key]
    return None


def _trim(grid: list[list]) -> list[list]:
    # Like the real API: drop trailing empty cells and rows, "" for interior blanks.
    rows = []
    for row in grid:
        while row and row[-1] is None:
            row = row[:-1]
        rows.append(["" if value is None else value for value in row])
    while rows and not rows[-1]:
        rows.pop()
    return rows


class FakeGoogle:
    def __init__(self):
        self.spreadsheets: dict[str, FakeSpreadsheet] = {}
        self.create_calls = 0
        self.create_delay = 0.0
        self.fail: dict[str, Exception] = {}

    # --- helpers --------------------------------------------------------------

    def _check(self, method: str) -> None:
        exc = self.fail.pop(method, None)
        if exc:
            raise exc

    def _get(self, spreadsheet_id: str) -> FakeSpreadsheet:
        spreadsheet = self.spreadsheets.get(spreadsheet_id)
        if spreadsheet is None or spreadsheet.deleted:
            raise GoogleApiError(404, "Requested entity was not found.")
        return spreadsheet

    def _location(self, spreadsheet: FakeSpreadsheet, meta: dict) -> dict:
        if meta["kind"] == "spreadsheet":
            return {"locationType": "SPREADSHEET", "spreadsheet": True}
        if meta["kind"] == "sheet":
            return {"locationType": "SHEET", "sheetId": meta["sheet_id"]}
        index = spreadsheet.tab(meta["sheet_id"]).columns.index(meta["column_id"])
        return {
            "locationType": "COLUMN",
            "dimensionRange": {
                "sheetId": meta["sheet_id"],
                "dimension": "COLUMNS",
                "startIndex": index,
                "endIndex": index + 1,
            },
        }

    # --- Drive ---------------------------------------------------------------

    async def get_file(self, file_id: str) -> dict:
        self._check("get_file")
        spreadsheet = self._get(file_id)
        return {"id": file_id, "trashed": spreadsheet.trashed}

    async def find_app_files(self, key: str, value: str) -> list[dict]:
        self._check("find_app_files")
        return [
            {"id": s.spreadsheet_id}
            for s in self.spreadsheets.values()
            if not s.deleted and not s.trashed and s.app_properties.get(key) == value
        ]

    async def set_app_properties(self, file_id: str, properties: dict[str, str]) -> None:
        self._check("set_app_properties")
        self._get(file_id).app_properties.update(properties)

    # --- Sheets --------------------------------------------------------------

    async def create_spreadsheet(self, body: dict) -> dict:
        self.create_calls += 1
        if self.create_delay:
            await asyncio.sleep(self.create_delay)
        self._check("create_spreadsheet")

        spreadsheet_id = f"sheet-{next(_ids)}"
        tabs = []
        for sheet in body.get("sheets", []):
            tab = FakeTab(sheet_id=next(_ids), title=sheet["properties"]["title"])
            for data in sheet.get("data", []):
                for r, row in enumerate(data.get("rowData", [])):
                    for c, cell in enumerate(row.get("values", [])):
                        tab.set_cell(data.get("startRow", 0) + r, data.get("startColumn", 0) + c, _cell_value(cell))
            tabs.append(tab)
        self.spreadsheets[spreadsheet_id] = FakeSpreadsheet(spreadsheet_id, body["properties"]["title"], tabs)
        return {
            "spreadsheetId": spreadsheet_id,
            "sheets": [{"properties": {"sheetId": t.sheet_id, "title": t.title}} for t in tabs],
        }

    async def get_sheet_properties(self, spreadsheet_id: str) -> list[dict]:
        self._check("get_sheet_properties")
        return [
            {
                "sheetId": tab.sheet_id,
                "title": tab.title,
                "gridProperties": {"rowCount": max(len(tab.rows), 1000), "columnCount": len(tab.columns)},
            }
            for tab in self._get(spreadsheet_id).tabs
        ]

    async def batch_update(self, spreadsheet_id: str, requests: list[dict]) -> dict:
        self._check("batch_update")
        spreadsheet = self._get(spreadsheet_id)
        replies = [self._apply(spreadsheet, request) for request in requests]
        return {"spreadsheetId": spreadsheet_id, "replies": replies}

    def _apply(self, spreadsheet: FakeSpreadsheet, request: dict) -> dict:
        (kind, body), = request.items()
        if kind == "createDeveloperMetadata":
            meta = body["developerMetadata"]
            location = meta["location"]
            entry = {"key": meta["metadataKey"], "value": meta["metadataValue"]}
            if location.get("spreadsheet"):
                entry.update(kind="spreadsheet")
            elif "sheetId" in location:
                spreadsheet.tab(location["sheetId"])
                entry.update(kind="sheet", sheet_id=location["sheetId"])
            else:
                dimension_range = location["dimensionRange"]
                tab = spreadsheet.tab(dimension_range["sheetId"])
                entry.update(
                    kind="column",
                    sheet_id=tab.sheet_id,
                    column_id=tab.column_at(dimension_range["startIndex"]),
                )
            spreadsheet.metadata.append(entry)
            return {}
        if kind == "appendCells":
            tab = spreadsheet.tab(body["sheetId"])
            for row in body["rows"]:
                row_index = len(tab.rows)
                tab.rows.append({})
                for c, cell in enumerate(row.get("values", [])):
                    tab.set_cell(row_index, c, _cell_value(cell))
            return {}
        if kind == "deleteDimension":
            dimension_range = body["range"]
            tab = spreadsheet.tab(dimension_range["sheetId"])
            start, end = dimension_range["startIndex"], dimension_range["endIndex"]
            if dimension_range["dimension"] == "ROWS":
                del tab.rows[start:end]
            else:
                self._delete_columns(spreadsheet, tab, start, end)
            return {}
        if kind == "appendDimension":
            tab = spreadsheet.tab(body["sheetId"])
            assert body["dimension"] == "COLUMNS"
            for _ in range(body["length"]):
                tab.add_column()
            return {}
        if kind == "addSheet":
            properties = body["properties"]
            tab = FakeTab(sheet_id=properties.get("sheetId", next(_ids)), title=properties["title"])
            spreadsheet.tabs.append(tab)
            return {"addSheet": {"properties": {"sheetId": tab.sheet_id, "title": tab.title}}}
        if kind == "updateCells":
            start = body["start"]
            tab = spreadsheet.tab(start["sheetId"])
            for r, row in enumerate(body["rows"]):
                for c, cell in enumerate(row.get("values", [])):
                    tab.set_cell(start.get("rowIndex", 0) + r, start.get("columnIndex", 0) + c, _cell_value(cell))
            return {}
        if kind in ("setDataValidation", "repeatCell", "addProtectedRange", "updateSheetProperties"):
            spreadsheet.other_requests.append(request)
            return {}
        raise NotImplementedError(f"FakeGoogle does not handle {kind}")

    @staticmethod
    def _delete_columns(spreadsheet: FakeSpreadsheet, tab: FakeTab, start: int, end: int) -> None:
        removed = set(tab.columns[start:end])
        tab.columns = [c for c in tab.columns if c not in removed]
        for row in tab.rows:
            for column_id in removed:
                row.pop(column_id, None)
        spreadsheet.metadata = [m for m in spreadsheet.metadata if m.get("column_id") not in removed]

    async def search_developer_metadata(self, spreadsheet_id: str, keys: list[str]) -> list[dict]:
        self._check("search_developer_metadata")
        spreadsheet = self._get(spreadsheet_id)
        return [
            {
                "metadataKey": meta["key"],
                "metadataValue": meta["value"],
                "visibility": "PROJECT",
                "location": self._location(spreadsheet, meta),
            }
            for meta in spreadsheet.metadata
            if meta["key"] in keys
        ]

    async def get_grid_values(self, spreadsheet_id: str, grid_range: dict) -> list[list]:
        self._check("get_grid_values")
        tab = self._get(spreadsheet_id).tab(grid_range["sheetId"])
        grid = tab.grid()
        rows = grid[grid_range.get("startRowIndex", 0):grid_range.get("endRowIndex")]
        start_col = grid_range.get("startColumnIndex", 0)
        end_col = grid_range.get("endColumnIndex")
        return _trim([row[start_col:end_col] for row in rows])

    async def update_grid_values(self, spreadsheet_id: str, updates: list[tuple[dict, list[list]]]) -> None:
        self._check("update_grid_values")
        spreadsheet = self._get(spreadsheet_id)
        for grid_range, values in updates:
            tab = spreadsheet.tab(grid_range["sheetId"])
            for r, row in enumerate(values):
                for c, value in enumerate(row):
                    tab.set_cell(grid_range.get("startRowIndex", 0) + r, grid_range.get("startColumnIndex", 0) + c, value)

    # --- simulated user edits in the Sheets UI ---------------------------------

    def only(self) -> FakeSpreadsheet:
        (spreadsheet,) = [s for s in self.spreadsheets.values() if not s.deleted]
        return spreadsheet

    def main_tab(self, spreadsheet: FakeSpreadsheet | None = None) -> FakeTab:
        # The original tab; duplicates get higher sheetIds.
        return min((spreadsheet or self.only()).tabs, key=lambda tab: tab.sheet_id)

    def header_index(self, header: str, tab: FakeTab | None = None) -> int:
        tab = tab or self.main_tab()
        return tab.grid()[0].index(header)

    def rename_header(self, header: str, new_header: str) -> None:
        tab = self.main_tab()
        tab.set_cell(0, self.header_index(header, tab), new_header)

    def move_column(self, header: str, to_index: int) -> None:
        tab = self.main_tab()
        column_id = tab.columns.pop(self.header_index(header, tab))
        tab.columns.insert(to_index, column_id)

    def rename_tab(self, title: str) -> None:
        self.main_tab().title = title

    def insert_column(self, index: int, header: str, fill: Any = None) -> None:
        tab = self.main_tab()
        column_id = tab.add_column(index)
        tab.rows[0][column_id] = header
        if fill is not None:
            for row in tab.rows[1:]:
                row[column_id] = fill

    def delete_column(self, header: str) -> None:
        spreadsheet = self.only()
        tab = self.main_tab(spreadsheet)
        index = self.header_index(header, tab)
        self._delete_columns(spreadsheet, tab, index, index + 1)

    def sort_rows(self, header: str, reverse: bool = False) -> None:
        tab = self.main_tab()
        column_id = tab.columns[self.header_index(header, tab)]
        tab.rows[1:] = sorted(tab.rows[1:], key=lambda row: str(row.get(column_id, "")), reverse=reverse)

    def duplicate_tab(self) -> FakeTab:
        spreadsheet = self.only()
        source = self.main_tab(spreadsheet)
        copy_tab = FakeTab(sheet_id=next(_ids), title=f"Copy of {source.title}")
        id_map = {}
        for column_id in source.columns:
            id_map[column_id] = copy_tab.add_column()
        copy_tab.rows = [{id_map[c]: v for c, v in row.items()} for row in source.rows]
        for meta in list(spreadsheet.metadata):
            if meta.get("sheet_id") == source.sheet_id:
                clone = copy.deepcopy(meta)
                clone["sheet_id"] = copy_tab.sheet_id
                if "column_id" in clone:
                    clone["column_id"] = id_map[clone["column_id"]]
                spreadsheet.metadata.append(clone)
        # Sheets inserts the copy right after the source; it must not become "the" tab.
        spreadsheet.tabs.insert(0, copy_tab)
        return copy_tab

    def values_by_header(self, tab: FakeTab | None = None) -> list[dict]:
        grid = (tab or self.main_tab()).grid()
        headers = grid[0]
        return [
            {header: value for header, value in zip(headers, row) if header is not None}
            for row in grid[1:]
        ]

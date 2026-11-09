"""batch_update requests that repair a spreadsheet's schema.

repair() (POST /api/v1/storage/repair) calls these when a tab or a required
column has gone missing; they are pure request builders so the repair flow only
has to decide *when* to run them.
"""
from services.google_sheets.schema import (
    BACKFILL_FIELDS,
    FIELD_BY_NAME,
    FIELDS,
    REVALIDATED_FIELDS,
    TAB_TITLE,
    build_tab_requests,
    column_requests,
    header_cell,
    validation_requests,
)


def new_tab_requests(properties: list[dict]) -> list[dict]:
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


def missing_column_requests(sheet_id: int, columns: dict[str, int], properties: list[dict]) -> list[dict]:
    missing = [
        field for field in FIELDS
        if (field.required or field.name in BACKFILL_FIELDS) and field.name not in columns
    ]
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
    # Older sheets were set up with other dropdowns (no Cash/Bank, no "manual");
    # this runs once, together with adding the columns those sheets lack.
    for name in REVALIDATED_FIELDS:
        if name in columns:
            requests.extend(validation_requests(sheet_id, FIELD_BY_NAME[name], columns[name]))
    return requests

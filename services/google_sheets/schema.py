from dataclasses import dataclass

SPREADSHEET_TITLE = "AI Expense Tracker"
TAB_TITLE = "Transactions"

# Drive appProperty used to find our spreadsheet again if target_id is lost.
APP_PROPERTY_KEY = "app"
APP_PROPERTY_VALUE = "ai-expense-tracker"

# Developer Metadata is the source of truth for where things are: it follows a
# column when it's renamed or moved and a tab when it's renamed.
META_SHEET_KEY = "et_sheet"
META_SHEET_VALUE = "transactions"
META_FIELD_KEY = "et_field"
META_SCHEMA_KEY = "et_schema_version"
SCHEMA_VERSION = "1"

TYPE_VALUES = ("expense", "income")
SOURCE_VALUES = ("text", "slip", "voice")


@dataclass(frozen=True)
class SheetField:
    name: str
    header: str
    required: bool


FIELDS = (
    SheetField("id", "ID", required=True),
    SheetField("date", "Date", required=True),
    SheetField("type", "Type", required=True),
    SheetField("category", "Category", required=False),
    SheetField("description", "Description", required=False),
    SheetField("amount", "Amount", required=True),
    SheetField("payment_channel", "Payment Channel", required=False),
    SheetField("source", "Source", required=False),
)
FIELD_BY_NAME = {field.name: field for field in FIELDS}
REQUIRED_FIELDS = tuple(field.name for field in FIELDS if field.required)


def _metadata(key: str, value: str, location: dict) -> dict:
    return {
        "createDeveloperMetadata": {
            "developerMetadata": {
                "metadataKey": key,
                "metadataValue": value,
                "visibility": "PROJECT",
                "location": location,
            }
        }
    }


def _column_range(sheet_id: int, index: int, start_row: int = 1) -> dict:
    return {
        "sheetId": sheet_id,
        "startRowIndex": start_row,
        "startColumnIndex": index,
        "endColumnIndex": index + 1,
    }


def _one_of(sheet_id: int, index: int, values: tuple[str, ...]) -> dict:
    return {
        "setDataValidation": {
            "range": _column_range(sheet_id, index),
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": value} for value in values],
                },
                "strict": True,
                "showCustomUi": True,
            },
        }
    }


def _number_format(sheet_id: int, index: int, format_type: str, pattern: str) -> dict:
    return {
        "repeatCell": {
            "range": _column_range(sheet_id, index),
            "cell": {"userEnteredFormat": {"numberFormat": {"type": format_type, "pattern": pattern}}},
            "fields": "userEnteredFormat.numberFormat",
        }
    }


def header_cell(field: SheetField) -> dict:
    return {"userEnteredValue": {"stringValue": field.header}}


def column_requests(sheet_id: int, field: SheetField, index: int) -> list[dict]:
    """Metadata, validation and format for one of our columns at `index`."""
    requests = [
        _metadata(
            META_FIELD_KEY,
            field.name,
            {"dimensionRange": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": index, "endIndex": index + 1}},
        )
    ]
    if field.name == "type":
        requests.append(_one_of(sheet_id, index, TYPE_VALUES))
    elif field.name == "source":
        requests.append(_one_of(sheet_id, index, SOURCE_VALUES))
    elif field.name == "amount":
        requests.append(_number_format(sheet_id, index, "NUMBER", "#,##0.00"))
    elif field.name == "date":
        requests.append(_number_format(sheet_id, index, "DATE", "yyyy-mm-dd"))
    return requests


def build_spreadsheet_body() -> dict:
    """spreadsheets.create body: one tab with the header row, row 1 frozen."""
    return {
        "properties": {"title": SPREADSHEET_TITLE},
        "sheets": [
            {
                "properties": {"title": TAB_TITLE, "gridProperties": {"frozenRowCount": 1}},
                "data": [
                    {
                        "startRow": 0,
                        "startColumn": 0,
                        "rowData": [{"values": [header_cell(field) for field in FIELDS]}],
                    }
                ],
            }
        ],
    }


def build_tab_requests(sheet_id: int) -> list[dict]:
    """Tab-level metadata, per-column setup and the soft header protection."""
    requests = [_metadata(META_SHEET_KEY, META_SHEET_VALUE, {"sheetId": sheet_id})]
    for index, field in enumerate(FIELDS):
        requests.extend(column_requests(sheet_id, field, index))
    # warningOnly: the owner can always edit; this only nudges against accidents.
    requests.append({
        "addProtectedRange": {
            "protectedRange": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "description": "Headers used by AI Expense Tracker",
                "warningOnly": True,
            }
        }
    })
    return requests


def build_setup_requests(sheet_id: int) -> list[dict]:
    """batchUpdate requests run once after spreadsheets.create."""
    return [
        _metadata(META_SCHEMA_KEY, SCHEMA_VERSION, {"spreadsheet": True}),
        *build_tab_requests(sheet_id),
    ]

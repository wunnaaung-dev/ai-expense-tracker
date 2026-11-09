"""Characterization tests for the Sheets codec/layout helpers.

They pin today's behaviour (cell shapes, round trips, metadata reading) so the
refactor of google_sheets_storage.py cannot change it silently.
"""
from datetime import date

import pytest

from constants.currencies import DEFAULT_CURRENCY
from schemas.expense import ExpenseData
from services.google_sheets.google_sheets_storage import (
    _columns_on,
    _field_value,
    _parse_row,
    _sheet_ids,
    _to_cell,
    _to_raw,
)
from services.google_sheets.schema import (
    CURRENCY_AMOUNT_FIELDS,
    FIELDS,
    META_FIELD_KEY,
    META_SHEET_KEY,
    META_SHEET_VALUE,
)

COLUMNS = {field.name: index for index, field in enumerate(FIELDS)}

RECORD_ID = "6f9619ff-8b86-d011-b42d-00c04fc964ff"


def cell_value(cell: dict) -> str | float:
    """What Sheets gives back for a written cell: its value, or "" when empty."""
    entered = cell.get("userEnteredValue", {})
    return entered.get("numberValue", entered.get("stringValue", ""))


def write_row(record: ExpenseData) -> list:
    """The row Sheets would return if `record` were written through the codec."""
    row: list = [""] * len(FIELDS)
    for name, index in COLUMNS.items():
        row[index] = cell_value(_to_cell(name, _field_value(record, name)))
    return row


def full_record() -> ExpenseData:
    return ExpenseData(
        date=date(2026, 10, 5),
        amount=123.5,
        description="lunch",
        category="Food",
        id=RECORD_ID,
        type="income",
        payment_channel="Bank",
        source="manual",
        currency="USD",
    )


def column_meta(name: str, sheet_id: int, index: int) -> dict:
    return {
        "metadataKey": META_FIELD_KEY,
        "metadataValue": name,
        "location": {
            "dimensionRange": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": index, "endIndex": index + 1}
        },
    }


def sheet_meta(sheet_id: int) -> dict:
    return {"metadataKey": META_SHEET_KEY, "metadataValue": META_SHEET_VALUE, "location": {"sheetId": sheet_id}}


# --- round trip ----------------------------------------------------------------

def test_full_record_round_trips_through_the_codec():
    record = full_record()

    assert _parse_row(write_row(record), COLUMNS) == record


@pytest.mark.parametrize(
    "name",
    [field.name for field in FIELDS if field.name not in CURRENCY_AMOUNT_FIELDS],
)
def test_each_field_round_trips(name):
    record = full_record()

    parsed = _parse_row(write_row(record), COLUMNS)

    assert parsed is not None
    assert getattr(parsed, name) == getattr(record, name)


@pytest.mark.parametrize("name", sorted(CURRENCY_AMOUNT_FIELDS))
def test_amount_columns_hold_the_amount_only_in_their_own_currency(name):
    for currency in ("THB", "MMK", "USD"):
        record = full_record().model_copy(update={"currency": currency})

        raw = _to_raw(name, _field_value(record, name))
        row = write_row(record)

        assert raw == (123.5 if CURRENCY_AMOUNT_FIELDS[name] == currency else "")
        assert row[COLUMNS[name]] == raw


def test_blank_cells_come_back_as_reader_defaults():
    record = ExpenseData(date=date(2026, 10, 5), amount=10, description="taxi", category="")

    parsed = _parse_row(write_row(record), COLUMNS)

    assert parsed is not None
    assert parsed.category == "General"  # a blank cell, not the record's empty string
    assert parsed.type == "expense"
    assert parsed.id is None
    assert parsed.payment_channel is None
    assert parsed.source is None
    assert parsed.currency == DEFAULT_CURRENCY  # rows from before currencies existed are THB


def test_unreadable_date_or_amount_is_skipped(caplog):
    row = write_row(full_record())
    row[COLUMNS["amount"]] = "not a number"

    assert _parse_row(row, COLUMNS) is None
    assert "unreadable date/amount" in caplog.text


def test_cell_shapes():
    assert _to_cell("date", date(2026, 10, 1)) == {
        "userEnteredValue": {"numberValue": (date(2026, 10, 1) - date(1899, 12, 30)).days}
    }
    assert _to_cell("amount", 10) == {"userEnteredValue": {"numberValue": 10.0}}
    assert _to_cell("description", "lunch") == {"userEnteredValue": {"stringValue": "lunch"}}
    assert _to_cell("currency", None) == {}  # an empty cell writes nothing


# --- developer metadata --------------------------------------------------------

def test_columns_on_keeps_the_leftmost_of_two_tagged_columns(caplog):
    metadata = [
        column_meta("amount", 7, 9),
        column_meta("amount", 7, 3),  # the same field tagged twice: leftmost wins
        column_meta("amount", 8, 0),  # a duplicated tab's copy: another sheet
        column_meta("description", 7, 4),
        sheet_meta(7),  # not a field tag at all
    ]

    columns = _columns_on(metadata, 7)

    assert columns == {"amount": 3, "description": 4}
    assert "using the leftmost" in caplog.text


def test_sheet_ids_orders_duplicated_tabs_lowest_first():
    metadata = [
        sheet_meta(12),
        {"metadataKey": META_SHEET_KEY, "metadataValue": "something-else", "location": {"sheetId": 1}},
        column_meta("amount", 5, 0),
        {"metadataKey": META_SHEET_KEY, "metadataValue": META_SHEET_VALUE, "location": {}},  # no sheetId
        sheet_meta(4),
    ]

    assert _sheet_ids(metadata) == [4, 12]

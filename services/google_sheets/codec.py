"""Cell codec: Python values <-> Sheets cell values, and a raw row -> ExpenseData.

The written form is what the Sheets API stores; the read form is what the rest
of the app consumes. Both directions live here so they can be tested without a
spreadsheet.
"""
import logging
from datetime import date, timedelta
from typing import Any

from constants.currencies import CURRENCIES, DEFAULT_CURRENCY
from schemas.expense import ExpenseData
from services.google_sheets.schema import (
    CURRENCY_AMOUNT_FIELDS,
    SOURCE_VALUES,
    TYPE_VALUES,
)

logger = logging.getLogger(__name__)

# Google Sheets counts dates as days since 1899-12-30.
SHEETS_EPOCH = date(1899, 12, 30)


def _to_raw(field: str, value: Any) -> Any:
    """Python value -> raw cell value (dates as serial numbers)."""
    if value is None:
        return ""
    if field == "date":
        if isinstance(value, str):
            value = date.fromisoformat(value)
        return (value - SHEETS_EPOCH).days
    if field == "amount" or field in CURRENCY_AMOUNT_FIELDS:
        return float(value)
    return str(value)


def _field_value(record: ExpenseData, field: str) -> Any:
    """The value written to a column; per-currency amounts are blank unless the currency matches."""
    if field in CURRENCY_AMOUNT_FIELDS:
        return record.amount if record.currency == CURRENCY_AMOUNT_FIELDS[field] else None
    return getattr(record, field)


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


def _one_of(value: Any, allowed: tuple, default: Any) -> Any:
    """`value` when it's one of `allowed`, else the reader's default."""
    return value if value in allowed else default


def parse_row(row: list, columns: dict[str, int]) -> ExpenseData | None:
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
    currency = get("currency")
    return ExpenseData(
        date=expense_date,
        amount=amount,
        description=str(get("description") or ""),
        category=str(get("category") or "General"),
        id=str(record_id) if record_id is not None else None,
        type=_one_of(record_type, TYPE_VALUES, "expense"),
        payment_channel=str(payment_channel) if payment_channel is not None else None,
        source=_one_of(source, SOURCE_VALUES, None),
        # Rows from before currencies existed (or a blank cell) are THB.
        currency=_one_of(currency, CURRENCIES, DEFAULT_CURRENCY),
    )

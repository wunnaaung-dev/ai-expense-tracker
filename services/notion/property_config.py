from typing import Any, Callable, Dict
from datetime import date
from constants.currencies import CURRENCIES, DEFAULT_CURRENCY
from schemas.expense import ExpenseData

CURRENCY_PROPERTY = "Currency"
TYPE_PROPERTY = "Type"
TYPE_VALUES = ("expense", "income")

EXPENSE_DB_SCHEMA: Dict[str, Dict[str, Any]] = {
    "Name": {"title": {}},
    "Amount": {"number": {}},
    "Date": {"date": {}},
    "Category": {"select": {}},
    CURRENCY_PROPERTY: {"select": {"options": [{"name": code} for code in CURRENCIES]}},
    TYPE_PROPERTY: {"select": {"options": [{"name": value} for value in TYPE_VALUES]}},
}
# Properties that databases created before they existed get on their next write.
BACKFILL_PROPERTIES = (CURRENCY_PROPERTY, TYPE_PROPERTY)

EXPENSE_PROPERTY_MAP: Dict[str, Callable[[ExpenseData], Any]] = {
    "Name": lambda item: {"title": [{"text": {"content": item.description}}]},
    "Amount": lambda item: {"number": item.amount},
    "Date": lambda item: {"date": {"start": item.date.isoformat()}},
    "Category": lambda item: {"select": {"name": item.category}},
    CURRENCY_PROPERTY: lambda item: {"select": {"name": item.currency or DEFAULT_CURRENCY}},
    TYPE_PROPERTY: lambda item: {"select": {"name": item.type}},
}

def build_properties(item: ExpenseData) -> Dict[str, Any]:
    return {
        column_name: value_fn(item)
        for column_name, value_fn in EXPENSE_PROPERTY_MAP.items()
    }

def parse_notion_page(page: Dict[str, Any]) -> ExpenseData:
    props = page.get("properties", {})

    title_prop = props.get("Name", {}).get("title", [])
    description = title_prop[0]["text"]["content"] if title_prop else ""

    amount = props.get("Amount", {}).get("number", 0) or 0

    date_str = props.get("Date", {}).get("date", {}).get("start", "")
    expense_date = date.fromisoformat(date_str) if date_str else date.today()

    category = props.get("Category", {}).get("select", {}).get("name", "General")

    # Pages from before currencies existed (or with it cleared) are THB.
    currency = (props.get(CURRENCY_PROPERTY, {}).get("select") or {}).get("name")

    # Pages from before income existed have no Type: they are expenses.
    record_type = (props.get(TYPE_PROPERTY, {}).get("select") or {}).get("name")

    return ExpenseData(
        date=expense_date,
        amount=amount,
        description=description,
        category=category,
        type=record_type if record_type in TYPE_VALUES else "expense",
        currency=currency if currency in CURRENCIES else DEFAULT_CURRENCY,
    )

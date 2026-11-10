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

# (Notion property, the ExpenseData field it holds, how to encode that field's value).
# A single list so the create map and the partial-update map can never drift apart.
_EXPENSE_PROPERTIES: tuple[tuple[str, str, Callable[[ExpenseData], Any]], ...] = (
    ("Name", "description", lambda item: {"title": [{"text": {"content": item.description}}]}),
    ("Amount", "amount", lambda item: {"number": item.amount}),
    ("Date", "date", lambda item: {"date": {"start": item.date.isoformat()}}),
    ("Category", "category", lambda item: {"select": {"name": item.category}}),
    (CURRENCY_PROPERTY, "currency", lambda item: {"select": {"name": item.currency or DEFAULT_CURRENCY}}),
    (TYPE_PROPERTY, "type", lambda item: {"select": {"name": item.type}}),
)

EXPENSE_PROPERTY_MAP: Dict[str, Callable[[ExpenseData], Any]] = {
    name: encode for name, _field, encode in _EXPENSE_PROPERTIES
}

# ExpenseData field -> the Notion property that holds it, so a PATCH writes only what changed.
PROPERTY_BY_FIELD: Dict[str, str] = {field: name for name, field, _encode in _EXPENSE_PROPERTIES}

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
        # The page id is the record's id: it is how a single entry is addressed for update/delete.
        id=str(page["id"]) if page.get("id") else None,
        type=record_type if record_type in TYPE_VALUES else "expense",
        currency=currency if currency in CURRENCIES else DEFAULT_CURRENCY,
    )

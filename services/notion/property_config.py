from typing import Any, Callable, Dict
from datetime import date
from schemas.expense import ExpenseData

EXPENSE_DB_SCHEMA: Dict[str, Dict[str, Any]] = {
    "Name": {"title": {}},
    "Amount": {"number": {}},
    "Date": {"date": {}},
    "Category": {"select": {}},
}

EXPENSE_PROPERTY_MAP: Dict[str, Callable[[ExpenseData], Any]] = {
    "Name": lambda item: {"title": [{"text": {"content": item.description}}]},
    "Amount": lambda item: {"number": item.amount},
    "Date": lambda item: {"date": {"start": item.date.isoformat()}},
    "Category": lambda item: {"select": {"name": item.category}}
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

    return ExpenseData(
        date=expense_date,
        amount=amount,
        description=description,
        category=category
    )

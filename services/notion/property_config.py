from typing import Any, Callable, Dict
from schemas.expense import ExpenseData

EXPENSE_PROPERTY_MAP: Dict[str, Callable[[ExpenseData], Any]] = {
    "Name": lambda item: {"title": [{"text": {"content": item.description}}]},
    "Amount": lambda item: {"number": item.amount},
    "Date": lambda item: {"date": {"start": item.date.isoformat()}},
}

def build_properties(item: ExpenseData) -> Dict[str, Any]:
    return {
        column_name: value_fn(item)
        for column_name, value_fn in EXPENSE_PROPERTY_MAP.items()
    }
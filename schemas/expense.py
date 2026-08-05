from typing import Literal, Optional, List

from pydantic import BaseModel
from datetime import date

class ExpenseData(BaseModel):
    date: date
    amount: float
    description: str
    category: str = "General"

class TextExpenseRequest(BaseModel):
    text: str

class BatchResult(BaseModel):
    filename: str
    status: Literal["success", "failed"]
    data: ExpenseData | None = None
    error: str | None = None

class ExpenseListResponse(BaseModel):
    expenses: List[ExpenseData]
    next_cursor: Optional[str] = None


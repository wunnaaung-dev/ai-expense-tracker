from typing import Literal, Optional, List

from pydantic import BaseModel
from datetime import date

ExpenseType = Literal["expense", "income"]
ExpenseSource = Literal["text", "slip", "voice"]

class ExpenseData(BaseModel):
    date: date
    amount: float
    description: str
    category: str = "General"
    # Optional until every backend and extraction prompt supplies them.
    id: Optional[str] = None
    type: ExpenseType = "expense"
    payment_channel: Optional[str] = None
    source: Optional[ExpenseSource] = None

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


from typing import Literal

from pydantic import BaseModel
from datetime import date

class ExpenseData(BaseModel):
    date: date
    amount: float
    description: str
    category: str = "General"

class BatchResult(BaseModel):
    filename: str
    status: Literal["success", "failed"]
    data: ExpenseData | None = None
    error: str | None = None
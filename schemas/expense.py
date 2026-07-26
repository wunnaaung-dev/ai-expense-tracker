from pydantic import BaseModel
from datetime import date

class ExpenseData(BaseModel):
    date: date
    amount: float
    description: str
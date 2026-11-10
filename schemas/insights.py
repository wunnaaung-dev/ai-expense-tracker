from datetime import date, datetime
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from schemas.expense import Currency, ExpenseType

PeriodKind = Literal["week", "month", "year", "custom"]
Granularity = Literal["day", "week", "month"]
ChannelName = Literal["Cash", "Bank", "Other"]


class PeriodInfo(BaseModel):
    kind: PeriodKind
    start: date
    end: date
    granularity: Granularity


class RangeInfo(BaseModel):
    start: date
    end: date


class Change(BaseModel):
    current: float
    previous: float
    # None when the previous period is 0 (nothing to compare with).
    change_pct: Optional[float]


class Totals(BaseModel):
    income: Change
    expense: Change
    net: Change
    income_count: int
    expense_count: int
    # Expenses as a % of income; None when there is no income.
    spent_pct: Optional[float]
    previous_spent_pct: Optional[float]


class Bucket(BaseModel):
    start: date
    end: date
    income: float
    expense: float
    net: float
    count: int


class CategoryShare(BaseModel):
    name: str
    amount: float
    count: int
    share_pct: Optional[float]
    change_pct: Optional[float]


class Categories(BaseModel):
    expense: List[CategoryShare]
    income: List[CategoryShare]


class ChannelShare(BaseModel):
    name: ChannelName
    amount: float
    count: int
    share_pct: Optional[float]


# --- "What changed": facts only; the frontend words them ------------------------------

class CategoryChangeHighlight(BaseModel):
    kind: Literal["category_change"] = "category_change"
    type: ExpenseType
    category: str
    current: float
    previous: float
    change_pct: float


class RatioHighlight(BaseModel):
    kind: Literal["ratio"] = "ratio"
    spent_pct: float
    previous_spent_pct: Optional[float]


class BiggestBucketHighlight(BaseModel):
    kind: Literal["biggest_bucket"] = "biggest_bucket"
    start: date
    end: date
    expense: float


Highlight = Annotated[
    Union[CategoryChangeHighlight, RatioHighlight, BiggestBucketHighlight],
    Field(discriminator="kind"),
]


class MonthTotal(BaseModel):
    income: float
    expense: float
    net: float
    spent_pct: Optional[float]
    count: int


class MonthRow(MonthTotal):
    month: str  # YYYY-MM
    # The month isn't over yet.
    to_date: bool


class MonthlyReport(BaseModel):
    months: List[MonthRow]
    total: MonthTotal


class InsightsResponse(BaseModel):
    currency: Currency
    # When the records were read from storage (they may come from the short-lived cache).
    generated_at: datetime
    # False when the user has no records at all, in any currency.
    has_entries: bool
    # Every currency present in the records, before the currency filter (sorted).
    available_currencies: List[Currency]
    period: PeriodInfo
    previous: RangeInfo
    totals: Totals
    buckets: List[Bucket]
    categories: Categories
    payment_channels: List[ChannelShare]
    highlights: List[Highlight]
    monthly: MonthlyReport

from datetime import date, datetime
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from schemas.expense import Currency
from schemas.insights import CategoryChangeHighlight, CategoryShare, Change, RangeInfo, RatioHighlight, Totals

DashboardPeriod = Literal["week", "month", "year"]


class DashboardPeriodInfo(BaseModel):
    kind: DashboardPeriod
    start: date
    end: date


class FlowDay(BaseModel):
    date: date
    expense: float
    # Expense entries that day (income isn't counted).
    count: int


class SpendingFlow(BaseModel):
    """The last 7 days whatever the period, oldest first, compared with the 7 days before."""
    start: date
    end: date
    days: List[FlowDay]
    expense: Change
    # The day with the most spending; None when nothing was spent.
    peak: Optional[date]


DashboardInsight = Annotated[Union[CategoryChangeHighlight, RatioHighlight], Field(discriminator="kind")]


class DashboardResponse(BaseModel):
    """Home: the user's default currency only."""
    currency: Currency
    generated_at: datetime
    has_entries: bool
    period: DashboardPeriodInfo
    previous: RangeInfo
    # Balance card and "This month vs last" (same days of the previous period).
    totals: Totals
    spending_flow: SpendingFlow
    # Expense categories of the period, amount desc.
    categories: List[CategoryShare]
    # The tip under "vs last": the biggest category move, else the income ratio; None when neither applies.
    insight: Optional[DashboardInsight]

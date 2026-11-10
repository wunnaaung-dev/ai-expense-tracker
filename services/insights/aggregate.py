from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional

from constants.payment_channels import PAYMENT_CHANNELS
from schemas.dashboard import DashboardPeriodInfo, DashboardResponse, FlowDay, SpendingFlow
from schemas.expense import Currency, EntryFilter, ExpenseData, EntryType
from schemas.insights import (
    BiggestBucketHighlight,
    Bucket,
    Categories,
    CategoryChangeHighlight,
    CategoryShare,
    Change,
    ChannelShare,
    Highlight,
    InsightsResponse,
    MonthlyReport,
    MonthRow,
    MonthTotal,
    PeriodInfo,
    RangeInfo,
    RatioHighlight,
    Totals,
)
from services.insights.periods import DateRange, Period
from services.storage.filters import matches


def _pct(part: float, whole: float) -> Optional[float]:
    return None if whole == 0 else round(part / whole * 100, 1)


def _change_pct(current: float, previous: float) -> Optional[float]:
    # abs(): a net going from -100 to +50 is up (+150%), not down.
    return None if previous == 0 else round((current - previous) / abs(previous) * 100, 1)


class _Sums:
    """Raw float sums; rounded only when they go into the response."""

    def __init__(self, records: List[ExpenseData]):
        self.income = sum(r.amount for r in records if r.type == "income")
        self.expense = sum(r.amount for r in records if r.type == "expense")
        self.income_count = sum(1 for r in records if r.type == "income")
        self.expense_count = len(records) - self.income_count

    @property
    def count(self) -> int:
        return self.income_count + self.expense_count


class _Builder:
    def __init__(self, records: List[ExpenseData], currency: Currency):
        self.currency = currency
        self.records = [r for r in records if matches(r, EntryFilter(currency=currency))]

    def money(self, value: float) -> float:
        # Amounts are stored as entered: MMK is whole numbers, THB/USD have 2 decimals.
        return float(round(value, 0 if self.currency == "MMK" else 2))

    def within(self, period: DateRange) -> List[ExpenseData]:
        filters = EntryFilter(date_from=period.start, date_to=period.end)
        return [r for r in self.records if matches(r, filters)]

    def change(self, current: float, previous: float) -> Change:
        return Change(
            current=self.money(current),
            previous=self.money(previous),
            change_pct=_change_pct(current, previous),
        )

    def totals(self, current: _Sums, previous: _Sums) -> Totals:
        return Totals(
            income=self.change(current.income, previous.income),
            expense=self.change(current.expense, previous.expense),
            net=self.change(current.income - current.expense, previous.income - previous.expense),
            income_count=current.income_count,
            expense_count=current.expense_count,
            spent_pct=_pct(current.expense, current.income),
            previous_spent_pct=_pct(previous.expense, previous.income),
        )

    def buckets(self, ranges: List[DateRange]) -> List[Bucket]:
        result = []
        for bucket in ranges:
            sums = _Sums(self.within(bucket))
            result.append(Bucket(
                start=bucket.start,
                end=bucket.end,
                income=self.money(sums.income),
                expense=self.money(sums.expense),
                net=self.money(sums.income - sums.expense),
                count=sums.count,
            ))
        return result

    def categories(self, current: List[ExpenseData], previous: List[ExpenseData],
                   entry_type: EntryType) -> List[CategoryShare]:
        amounts = _by_category(current, entry_type)
        counts: Dict[str, int] = defaultdict(int)
        for record in current:
            if record.type == entry_type:
                counts[record.category] += 1
        before = _by_category(previous, entry_type)
        whole = sum(amounts.values())
        ordered = sorted(amounts.items(), key=lambda item: (-item[1], item[0]))
        return [
            CategoryShare(
                name=name,
                amount=self.money(amount),
                count=counts[name],
                share_pct=_pct(amount, whole),
                change_pct=_change_pct(amount, before.get(name, 0.0)),
            )
            for name, amount in ordered
        ]

    def payment_channels(self, current: List[ExpenseData]) -> List[ChannelShare]:
        amounts: Dict[str, float] = {name: 0.0 for name in PAYMENT_CHANNELS}
        counts: Dict[str, int] = {name: 0 for name in PAYMENT_CHANNELS}
        for record in current:
            if record.type != "expense":
                continue
            # Rows written before channels were limited to Cash/Bank may hold free text.
            name = record.payment_channel if record.payment_channel in PAYMENT_CHANNELS else "Other"
            amounts[name] = amounts.get(name, 0.0) + record.amount
            counts[name] = counts.get(name, 0) + 1
        whole = sum(amounts.values())
        ordered = sorted(amounts, key=lambda name: -amounts[name])  # stable: Cash before Bank on a tie
        return [
            ChannelShare(name=name, amount=self.money(amounts[name]), count=counts[name],
                         share_pct=_pct(amounts[name], whole))
            for name in ordered
        ]

    def highlights(self, current: List[ExpenseData], previous: List[ExpenseData],
                   totals: Totals, current_sums: _Sums, buckets: List[Bucket]) -> List[Highlight]:
        result: List[Highlight] = []

        # The expense category whose spending moved the most (only those spent on before).
        now, before = _by_category(current, "expense"), _by_category(previous, "expense")
        def move(name: str) -> float:
            return abs(now.get(name, 0.0) - before[name])

        candidates = sorted(name for name, amount in before.items() if amount > 0 and self.money(move(name)) != 0)
        if candidates:
            name = max(candidates, key=move)  # alphabetically first on a tie
            result.append(CategoryChangeHighlight(
                type="expense",
                category=name,
                current=self.money(now.get(name, 0.0)),
                previous=self.money(before[name]),
                change_pct=_change_pct(now.get(name, 0.0), before[name]),
            ))

        if current_sums.income > 0:
            result.append(RatioHighlight(spent_pct=totals.spent_pct, previous_spent_pct=totals.previous_spent_pct))

        if len(buckets) >= 2:
            biggest = max(buckets, key=lambda b: b.expense)  # first one on a tie
            if biggest.expense > 0:
                result.append(BiggestBucketHighlight(start=biggest.start, end=biggest.end, expense=biggest.expense))
        return result

    def monthly(self, months: List[DateRange], today: date) -> MonthlyReport:
        rows = []
        for month in months:
            sums = _Sums(self.within(month))
            rows.append(MonthRow(
                month=month.start.strftime("%Y-%m"),
                to_date=month.end > today,
                income=self.money(sums.income),
                expense=self.money(sums.expense),
                net=self.money(sums.income - sums.expense),
                spent_pct=_pct(sums.expense, sums.income),
                count=sums.count,
            ))
        everything = _Sums(self.within(DateRange(months[0].start, months[-1].end)))
        total = MonthTotal(
            income=self.money(everything.income),
            expense=self.money(everything.expense),
            net=self.money(everything.income - everything.expense),
            spent_pct=_pct(everything.expense, everything.income),
            count=everything.count,
        )
        return MonthlyReport(months=rows, total=total)


def _by_category(records: List[ExpenseData], entry_type: EntryType) -> Dict[str, float]:
    amounts: Dict[str, float] = defaultdict(float)
    for record in records:
        if record.type == entry_type:
            amounts[record.category] += record.amount
    return dict(amounts)


def build_dashboard(
    records: List[ExpenseData],
    currency: Currency,
    period: Period,
    flow: Period,
    generated_at: datetime,
) -> DashboardResponse:
    """Home: the period's totals and expense categories, plus `flow` (the last 7 days, by day)."""
    builder = _Builder(records, currency)
    current = builder.within(period.current)
    previous = builder.within(period.previous)
    current_sums = _Sums(current)
    totals = builder.totals(current_sums, _Sums(previous))
    insights = builder.highlights(current, previous, totals, current_sums, buckets=[])

    # Spending only: income neither adds to a day nor counts as one of its entries.
    days = []
    for day in flow.buckets:
        sums = _Sums(builder.within(day))
        days.append(FlowDay(date=day.start, expense=builder.money(sums.expense), count=sums.expense_count))
    flow_now = _Sums(builder.within(flow.current)).expense
    flow_before = _Sums(builder.within(flow.previous)).expense
    peak = max(days, key=lambda d: d.expense)  # first one on a tie

    return DashboardResponse(
        currency=currency,
        generated_at=generated_at,
        has_entries=bool(records),
        period=DashboardPeriodInfo(kind=period.kind, start=period.current.start, end=period.current.end),
        previous=RangeInfo(start=period.previous.start, end=period.previous.end),
        totals=totals,
        spending_flow=SpendingFlow(
            start=flow.current.start,
            end=flow.current.end,
            days=days,
            expense=builder.change(flow_now, flow_before),
            peak=peak.date if peak.expense > 0 else None,
        ),
        categories=builder.categories(current, previous, "expense"),
        insight=insights[0] if insights else None,
    )


def build_insights(
    records: List[ExpenseData],
    currency: Currency,
    period: Period,
    months: List[DateRange],
    today: date,
    generated_at: datetime,
) -> InsightsResponse:
    """Everything the Insights page shows, from every record of the user (any currency, any date)."""
    builder = _Builder(records, currency)
    current = builder.within(period.current)
    previous = builder.within(period.previous)
    current_sums, previous_sums = _Sums(current), _Sums(previous)
    totals = builder.totals(current_sums, previous_sums)
    buckets = builder.buckets(period.buckets)

    return InsightsResponse(
        currency=currency,
        generated_at=generated_at,
        has_entries=bool(records),
        # Before the currency filter: which currencies this page could switch to.
        available_currencies=sorted({r.currency for r in records if r.currency}),
        period=PeriodInfo(kind=period.kind, start=period.current.start, end=period.current.end,
                          granularity=period.granularity),
        previous=RangeInfo(start=period.previous.start, end=period.previous.end),
        totals=totals,
        buckets=buckets,
        categories=Categories(
            expense=builder.categories(current, previous, "expense"),
            income=builder.categories(current, previous, "income"),
        ),
        payment_channels=builder.payment_channels(current),
        highlights=builder.highlights(current, previous, totals, current_sums, buckets),
        monthly=builder.monthly(months, today),
    )

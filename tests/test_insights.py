from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from core import clock
from core.dependencies import get_insights_service
from core.security import get_current_user
from main import app
from schemas.expense import EntryCreate, ExpenseData
from services.entries.entry_defaults import EntryDefaults
from services.entries.entry_service import EntryService
from services.insights.aggregate import build_insights
from services.insights.insights_service import InsightsService
from services.insights.periods import DateRange, buckets, month_ranges, resolve_period
from services.storage import record_cache
from tests.test_expense_schema import RecordingStorage

TODAY = date(2026, 10, 24)  # a Saturday
NOW = datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)


def rec(day: date, amount: float, type="expense", category="Food", currency="USD", channel="Bank"):
    return ExpenseData(date=day, amount=amount, description="", category=category, type=type,
                       currency=currency, payment_channel=channel)


def ranges(items):
    return [(r.start, r.end) for r in items]


# --- periods ------------------------------------------------------------------------

@pytest.mark.parametrize("week_start, start", [("monday", date(2026, 10, 19)), ("sunday", date(2026, 10, 18))])
def test_week_period(week_start, start):
    period = resolve_period("week", TODAY, week_start)
    assert (period.current.start, period.current.end) == (start, TODAY)
    assert period.previous == DateRange(date(start.year, 10, start.day - 7), date(2026, 10, 17))
    assert period.granularity == "day" and len(period.buckets) == period.current.days


def test_month_period_compares_same_days_and_uses_calendar_weeks():
    period = resolve_period("month", TODAY, "monday")
    assert period.current == DateRange(date(2026, 10, 1), TODAY)
    assert period.previous == DateRange(date(2026, 9, 1), date(2026, 9, 24))
    assert period.granularity == "week"
    # Oct 1 2026 is a Thursday: the first week is cut at the period start.
    assert ranges(period.buckets) == [
        (date(2026, 10, 1), date(2026, 10, 4)),
        (date(2026, 10, 5), date(2026, 10, 11)),
        (date(2026, 10, 12), date(2026, 10, 18)),
        (date(2026, 10, 19), TODAY),
    ]


def test_sunday_weeks():
    weeks = buckets(DateRange(date(2026, 10, 1), date(2026, 10, 10)), "week", "sunday")
    assert ranges(weeks) == [(date(2026, 10, 1), date(2026, 10, 3)), (date(2026, 10, 4), date(2026, 10, 10))]


def test_month_previous_clamps_to_shorter_month():
    period = resolve_period("month", date(2026, 3, 31), "monday")
    assert period.previous == DateRange(date(2026, 2, 1), date(2026, 2, 28))
    january = resolve_period("month", date(2026, 1, 15), "monday")
    assert january.previous == DateRange(date(2025, 12, 1), date(2025, 12, 15))


def test_year_period_clamps_leap_day():
    period = resolve_period("year", date(2028, 2, 29), "monday")
    assert period.current == DateRange(date(2028, 1, 1), date(2028, 2, 29))
    assert period.previous == DateRange(date(2027, 1, 1), date(2027, 2, 28))
    assert period.granularity == "month"
    assert ranges(period.buckets) == [(date(2028, 1, 1), date(2028, 1, 31)), (date(2028, 2, 1), date(2028, 2, 29))]


def test_leap_day_month_and_week_periods():
    # 2028-02-29 is a Tuesday: the previous month ends Jan 29, the previous week starts Feb 21.
    month = resolve_period("month", date(2028, 2, 29), "monday")
    assert month.current == DateRange(date(2028, 2, 1), date(2028, 2, 29))
    assert month.previous == DateRange(date(2028, 1, 1), date(2028, 1, 29))

    week = resolve_period("week", date(2028, 2, 29), "monday")
    assert week.current == DateRange(date(2028, 2, 28), date(2028, 2, 29))
    assert week.previous == DateRange(date(2028, 2, 21), date(2028, 2, 22))


def test_custom_previous_is_equal_length_range_before():
    period = resolve_period("custom", TODAY, "monday", date(2026, 10, 11), date(2026, 10, 20))
    assert period.previous == DateRange(date(2026, 10, 1), date(2026, 10, 10))


@pytest.mark.parametrize("days, granularity", [(14, "day"), (15, "week"), (92, "week"), (93, "month")])
def test_custom_granularity(days, granularity):
    start = date(2026, 1, 1)
    end = date.fromordinal(start.toordinal() + days - 1)
    assert resolve_period("custom", TODAY, "monday", start, end).granularity == granularity


def test_month_ranges():
    months = month_ranges(date(2026, 1, 10), 3)
    assert ranges(months) == [
        (date(2025, 11, 1), date(2025, 11, 30)),
        (date(2025, 12, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 31)),
    ]


# --- aggregation --------------------------------------------------------------------

def build(records, currency="USD", kind="month", months=3, **period_args):
    period = resolve_period(kind, TODAY, "monday", **period_args)
    return build_insights(records, currency, period, month_ranges(period.current.end, months), TODAY, NOW)


SAMPLE = [
    rec(date(2026, 10, 15), 300, type="income", category="Salary"),
    rec(date(2026, 10, 2), 10.10, category="Food"),
    rec(date(2026, 10, 16), 7.56, category="Food", channel="Cash"),
    rec(date(2026, 10, 20), 10.23, category="Transportation"),
    rec(date(2026, 9, 15), 300, type="income", category="Salary"),
    rec(date(2026, 9, 3), 15.77, category="Food"),
    rec(date(2026, 9, 10), 10.0, category="Transportation"),
    rec(date(2026, 9, 28), 99.0, category="Food"),  # after Sep 24: not in the comparison
    rec(date(2026, 10, 3), 5000, currency="MMK"),  # another currency: ignored
]


def test_totals_and_changes():
    body = build(SAMPLE)
    totals = body.totals
    assert totals.income.model_dump() == {"current": 300.0, "previous": 300.0, "change_pct": 0.0}
    assert totals.expense.current == 27.89 and totals.expense.previous == 25.77
    assert totals.expense.change_pct == 8.2
    assert totals.net.current == 272.11
    assert (totals.income_count, totals.expense_count) == (1, 3)
    assert totals.spent_pct == 9.3 and totals.previous_spent_pct == 8.6


def test_no_previous_and_no_income_give_null():
    body = build([rec(date(2026, 10, 2), 5)])
    assert body.totals.expense.change_pct is None
    assert body.totals.spent_pct is None and body.totals.previous_spent_pct is None
    assert body.categories.expense[0].change_pct is None


def test_net_change_from_a_negative_previous_is_up():
    records = [rec(date(2026, 9, 2), 100), rec(date(2026, 10, 2), 50, type="income", category="Salary")]
    assert build(records).totals.net.change_pct == 150.0


def test_mmk_rounds_to_whole_numbers():
    body = build([rec(date(2026, 10, 2), 1000.4, currency="MMK"), rec(date(2026, 10, 3), 1000.4, currency="MMK")],
                 currency="MMK")
    assert body.totals.expense.current == 2001.0


def test_currency_filter_and_legacy_records():
    legacy = ExpenseData(date=date(2026, 10, 2), amount=50, description="", currency="THB")  # no type → expense
    body = build(SAMPLE + [legacy], currency="THB")
    assert body.totals.expense.current == 50.0 and body.totals.income.current == 0.0
    assert body.has_entries


def test_available_currencies_lists_all_currencies_before_the_filter():
    # SAMPLE holds USD and MMK records; the filter only decides what is summed.
    assert build(SAMPLE).available_currencies == ["MMK", "USD"]
    only_mmk = build([rec(date(2026, 10, 3), 5000, currency="MMK")], currency="USD")
    assert only_mmk.available_currencies == ["MMK"] and only_mmk.has_entries
    assert build([]).available_currencies == []
    # A record without a currency can't be suggested.
    no_currency = ExpenseData(date=date(2026, 10, 2), amount=50, description="", currency=None)
    assert build([no_currency]).available_currencies == []


def test_buckets():
    body = build(SAMPLE)
    assert [(b.start.day, b.income, b.expense, b.count) for b in body.buckets] == [
        (1, 0.0, 10.1, 1), (5, 0.0, 0.0, 0), (12, 300.0, 7.56, 2), (19, 0.0, 10.23, 1),
    ]
    assert body.buckets[2].net == 292.44


def test_categories():
    body = build(SAMPLE)
    food, transport = body.categories.expense
    assert (food.name, food.amount, food.count, food.share_pct, food.change_pct) == ("Food", 17.66, 2, 63.3, 12.0)
    assert (transport.name, transport.amount, transport.change_pct) == ("Transportation", 10.23, 2.3)
    assert [c.name for c in body.categories.income] == ["Salary"]
    assert body.categories.income[0].share_pct == 100.0


def test_payment_channels():
    records = SAMPLE + [rec(date(2026, 10, 5), 2, channel="Card"), rec(date(2026, 10, 6), 1, channel=None)]
    channels = {c.name: (c.amount, c.count) for c in build(records).payment_channels}
    assert channels == {"Bank": (20.33, 2), "Cash": (7.56, 1), "Other": (3.0, 2)}
    only_bank = build([rec(date(2026, 10, 2), 4)]).payment_channels
    assert [(c.name, c.amount, c.share_pct) for c in only_bank] == [("Bank", 4.0, 100.0), ("Cash", 0.0, 0.0)]


def test_highlights():
    body = build(SAMPLE)
    category, ratio, biggest = body.highlights
    # Food moved +1.89, Transportation +0.23.
    assert (category.kind, category.category, category.current, category.previous, category.change_pct) == (
        "category_change", "Food", 17.66, 15.77, 12.0)
    assert (ratio.kind, ratio.spent_pct, ratio.previous_spent_pct) == ("ratio", 9.3, 8.6)
    assert (biggest.kind, biggest.start, biggest.expense) == ("biggest_bucket", date(2026, 10, 19), 10.23)


def test_highlights_skip_what_does_not_apply():
    assert build([]).highlights == []
    single_day = build([rec(TODAY, 3)], kind="custom", date_from=TODAY, date_to=TODAY)
    assert [h.kind for h in single_day.highlights] == []


def test_monthly_report():
    body = build(SAMPLE, months=2)
    september, october = body.monthly.months
    assert (september.month, september.to_date, september.expense, september.count) == ("2026-09", False, 124.77, 4)
    assert (october.month, october.to_date, october.income, october.spent_pct) == ("2026-10", True, 300.0, 9.3)
    assert body.monthly.total.income == 600.0 and body.monthly.total.count == 8


def test_empty():
    body = build([])
    assert not body.has_entries and body.totals.expense.current == 0.0
    assert body.categories.expense == [] and body.generated_at == NOW


# --- cache --------------------------------------------------------------------------

class CountingStorage(RecordingStorage):
    def __init__(self, records=()):
        super().__init__()
        self.records = list(records)
        self.reads = 0

    async def get_all(self, filters):
        self.reads += 1
        return list(self.records)


@pytest.fixture(autouse=True)
def empty_cache():
    record_cache.clear()
    yield
    record_cache.clear()


async def test_cache_hit_refresh_and_target_change():
    storage = CountingStorage([rec(TODAY, 1)])
    tag = ("google_sheets", "sheet-1")
    first, fetched = await record_cache.get_records(storage, 1, tag)
    again, fetched_again = await record_cache.get_records(storage, 1, tag)
    assert storage.reads == 1 and again == first and fetched_again == fetched

    await record_cache.get_records(storage, 1, tag, refresh=True)
    assert storage.reads == 2
    await record_cache.get_records(storage, 1, ("google_sheets", "sheet-2"))
    assert storage.reads == 3
    await record_cache.get_records(storage, 2, ("google_sheets", "sheet-2"))
    assert storage.reads == 4


async def test_saving_an_entry_clears_the_cache():
    storage = CountingStorage()
    tag = ("notion", "db")
    await record_cache.get_records(storage, 7, tag)
    user = SimpleNamespace(id=7, default_currency="USD", default_payment_channel="Bank")
    service = EntryService(
        image_extraction=None, text_extraction=None, audio_extraction=None, dedup=None,
        defaults=EntryDefaults(user), on_saved=lambda: record_cache.invalidate(7),
    )
    entry = EntryCreate(type="expense", amount=5, currency="USD", date=TODAY, category="Food",
                        payment_channel="Bank", source="manual")
    await service.save(entry, storage)
    await record_cache.get_records(storage, 7, tag)
    assert storage.reads == 2


async def test_clear_empties_the_cache_for_all_users():
    storage = CountingStorage([rec(TODAY, 1)])
    await record_cache.get_records(storage, 1, ("google_sheets", "sheet-1"))
    await record_cache.get_records(storage, 2, ("notion", "db"))
    assert storage.reads == 2

    record_cache.clear()
    await record_cache.get_records(storage, 1, ("google_sheets", "sheet-1"))
    await record_cache.get_records(storage, 2, ("notion", "db"))
    assert storage.reads == 4


async def test_refresh_replaces_the_cached_entry():
    storage = CountingStorage([rec(TODAY, 1)])
    tag = ("google_sheets", "sheet-1")
    await record_cache.get_records(storage, 1, tag)

    storage.records = [rec(TODAY, 9)]
    await record_cache.get_records(storage, 1, tag, refresh=True)
    assert storage.reads == 2

    again, _ = await record_cache.get_records(storage, 1, tag)
    assert storage.reads == 2  # the refreshed entry replaced the old one
    assert again == [rec(TODAY, 9)]


# --- router -------------------------------------------------------------------------

USER = SimpleNamespace(id=1, default_currency="USD", week_start="monday")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(clock, "today", lambda: TODAY)
    storage = CountingStorage(SAMPLE)
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_insights_service] = lambda: InsightsService(storage, USER, ("google_sheets", "s"))
    yield TestClient(app), storage
    app.dependency_overrides.clear()


def test_insights_endpoint(client):
    http, storage = client
    response = http.get("/api/v1/insights")
    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "USD"  # the user's default
    assert body["available_currencies"] == ["MMK", "USD"]  # every currency in the records
    assert body["period"] == {"kind": "month", "start": "2026-10-01", "end": "2026-10-24", "granularity": "week"}
    assert body["previous"] == {"start": "2026-09-01", "end": "2026-09-24"}
    assert len(body["monthly"]["months"]) == 3
    assert [h["kind"] for h in body["highlights"]] == ["category_change", "ratio", "biggest_bucket"]

    mmk = http.get("/api/v1/insights", params={"currency": "MMK", "period": "custom",
                                               "date_from": "2026-10-01", "date_to": "2026-10-10", "months": 1})
    assert mmk.status_code == 200 and mmk.json()["totals"]["expense"]["current"] == 5000.0
    assert storage.reads == 1  # the second call was served from the cache

    http.get("/api/v1/insights", params={"refresh": True})
    assert storage.reads == 2


@pytest.mark.parametrize("params", [
    {"period": "custom"},
    {"period": "custom", "date_from": "2026-10-01"},
    {"period": "custom", "date_from": "2026-10-10", "date_to": "2026-10-01"},
    {"period": "month", "date_from": "2026-10-01"},
    {"period": "day"},
    {"months": 0},
    {"months": 13},
    {"currency": "EUR"},
])
def test_insights_rejects_bad_params(client, params):
    http, _ = client
    assert http.get("/api/v1/insights", params=params).status_code == 422


def test_custom_range_over_the_limit_is_rejected(client):
    http, _ = client
    response = http.get("/api/v1/insights", params={"period": "custom",
                                                     "date_from": "2020-01-01", "date_to": "2025-12-31"})
    assert response.status_code == 422
    assert response.json() == {"code": "range_too_long", "max_days": 366}


def test_custom_range_limit_counts_both_ends(client):
    http, _ = client
    date_from = date(2028, 1, 1)
    # Both ends count: a full leap year (Jan 1 – Dec 31, 2028) is 366 days → allowed.
    leap_year = http.get("/api/v1/insights", params={"period": "custom", "date_from": date_from.isoformat(),
                                                      "date_to": (date_from + timedelta(days=365)).isoformat()})
    assert leap_year.status_code == 200
    # One day further (into 2029) is 367 → rejected.
    over = http.get("/api/v1/insights", params={"period": "custom", "date_from": date_from.isoformat(),
                                                 "date_to": (date_from + timedelta(days=366)).isoformat()})
    assert over.status_code == 422 and over.json() == {"code": "range_too_long", "max_days": 366}

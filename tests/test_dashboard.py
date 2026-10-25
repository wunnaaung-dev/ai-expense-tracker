from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from core import clock
from core.dependencies import get_insights_service
from core.security import get_current_user
from main import app
from services.insights.aggregate import build_dashboard
from services.insights.insights_service import InsightsService
from services.insights.periods import resolve_period
from services.storage import record_cache
from tests.test_insights import NOW, SAMPLE, TODAY, CountingStorage, rec


@pytest.fixture(autouse=True)
def empty_cache():
    record_cache.clear()
    yield
    record_cache.clear()


def build(records, currency="USD", kind="month"):
    period = resolve_period(kind, TODAY, "monday")
    flow = resolve_period("custom", TODAY, "monday", TODAY - timedelta(days=6), TODAY)
    return build_dashboard(records, currency, period, flow, NOW)


def test_totals_and_categories_follow_the_period():
    body = build(SAMPLE)
    assert (body.period.kind, body.period.start, body.period.end) == ("month", date(2026, 10, 1), TODAY)
    assert body.previous.start == date(2026, 9, 1) and body.previous.end == date(2026, 9, 24)
    assert body.totals.income.current == 300.0 and body.totals.expense.current == 27.89
    assert body.totals.expense.change_pct == 8.2 and body.totals.spent_pct == 9.3
    assert [(c.name, c.share_pct) for c in body.categories] == [("Food", 63.3), ("Transportation", 36.7)]

    week = build(SAMPLE, kind="week")
    assert week.period.start == date(2026, 10, 19) and week.totals.expense.current == 10.23


def test_spending_flow_is_the_last_seven_days():
    records = [
        rec(date(2026, 10, 18), 4),
        rec(date(2026, 10, 20), 6),
        rec(date(2026, 10, 20), 1, channel="Cash"),
        rec(date(2026, 10, 24), 3),
        rec(date(2026, 10, 22), 50, type="income", category="Salary"),  # income: not spending
        rec(date(2026, 10, 11), 10),  # the 7 days before (Oct 11–17)
        rec(date(2026, 10, 10), 99),  # older still: ignored
    ]
    flow = build(records, kind="year").spending_flow
    assert (flow.start, flow.end) == (date(2026, 10, 18), TODAY)
    assert [d.date.day for d in flow.days] == [18, 19, 20, 21, 22, 23, 24]
    assert [d.expense for d in flow.days] == [4.0, 0.0, 7.0, 0.0, 0.0, 0.0, 3.0]
    assert flow.days[2].count == 2 and flow.days[4].count == 0  # the 22nd only has income
    assert flow.expense.model_dump() == {"current": 14.0, "previous": 10.0, "change_pct": 40.0}
    assert flow.peak == date(2026, 10, 20)


def test_no_spending_has_no_peak():
    flow = build([rec(TODAY, 5, type="income", category="Salary")]).spending_flow
    assert flow.peak is None and flow.expense.change_pct is None


def test_insight_prefers_the_category_move_then_the_ratio():
    assert build(SAMPLE).insight.kind == "category_change"
    only_income = build([rec(date(2026, 10, 2), 100, type="income", category="Salary")])
    assert only_income.insight.kind == "ratio" and only_income.insight.spent_pct == 0.0
    assert build([]).insight is None


def test_empty():
    body = build([])
    assert not body.has_entries and body.categories == [] and len(body.spending_flow.days) == 7


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


def test_dashboard_endpoint_uses_default_currency_and_shares_the_cache(client):
    http, storage = client
    response = http.get("/api/v1/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "USD" and body["period"]["kind"] == "month"
    assert len(body["spending_flow"]["days"]) == 7
    assert body["insight"]["kind"] == "category_change"

    assert http.get("/api/v1/insights").status_code == 200
    assert storage.reads == 1  # Insights reused the records Home read


@pytest.mark.parametrize("params", [{"period": "custom"}, {"period": "day"}])
def test_dashboard_rejects_bad_period(client, params):
    http, _ = client
    assert http.get("/api/v1/dashboard", params=params).status_code == 422

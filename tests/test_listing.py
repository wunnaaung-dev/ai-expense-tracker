from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from core.dependencies import get_storage_service
from core.security import get_current_user
from main import app
from schemas.expense import EntryFilter, ExpenseData
from services.storage.filters import matches
from services.storage.pagination import paginate
from tests.test_currency import notion_with
from tests.test_expense_schema import RecordingStorage


def entry(description="lunch", day=1, **extra):
    return ExpenseData(date=date(2026, 10, day), amount=100, description=description, currency="THB", **extra)


# --- paginate ---------------------------------------------------------------------

def test_paginate_empty():
    page = paginate([], page=1, page_size=10)
    assert page.content == [] and page.total_elements == 0 and page.total_pages == 0
    assert page.first and page.last


def test_paginate_last_partial_page():
    records = [entry(f"e{i}") for i in range(5)]
    first = paginate(records, page=1, page_size=2)
    last = paginate(records, page=3, page_size=2)
    assert [r.description for r in first.content] == ["e0", "e1"]
    assert first.first and not first.last
    assert [r.description for r in last.content] == ["e4"]
    assert last.total_pages == 3 and last.total_elements == 5
    assert last.last and not last.first


def test_paginate_past_the_end_keeps_totals():
    page = paginate([entry()], page=4, page_size=10)
    assert page.content == [] and page.total_elements == 1 and page.total_pages == 1 and page.last


# --- matches ------------------------------------------------------------------------

@pytest.mark.parametrize("filters, expected", [
    (EntryFilter(), True),
    (EntryFilter(entry_type="income"), False),
    (EntryFilter(currency="MMK"), False),
    (EntryFilter(category="FOOD"), True),
    (EntryFilter(category="Transport"), False),
    (EntryFilter(date_from=date(2026, 10, 5)), True),
    (EntryFilter(date_from=date(2026, 10, 6)), False),
    (EntryFilter(date_to=date(2026, 10, 5)), True),
    (EntryFilter(date_to=date(2026, 10, 4)), False),
    (EntryFilter(search="  BEANS "), True),
    (EntryFilter(search="foo"), True),  # category "Food"
    (EntryFilter(search="tea"), False),
])
def test_matches(filters, expected):
    record = entry("Coffee beans", day=5, category="Food")
    assert matches(record, filters) is expected


# --- router -------------------------------------------------------------------------

class ListStorage(RecordingStorage):
    calls = []

    async def get_all(self, filters):
        self.calls.append(filters)
        return [entry(f"e{i}") for i in range(120)]


@pytest.fixture
def client():
    ListStorage.calls = []
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, default_currency="THB")
    app.dependency_overrides[get_storage_service] = ListStorage
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_returns_page_body(client):
    response = client.get("/api/v1/expense", params={"page_size": 50, "page": 3})
    assert response.status_code == 200
    body = response.json()
    assert {k: v for k, v in body.items() if k != "content"} == {
        "page": 3, "pageSize": 50, "totalElements": 120, "totalPages": 3, "first": False, "last": True,
    }
    assert len(body["content"]) == 20


def test_list_passes_filters(client):
    response = client.get("/api/v1/income", params={
        "search": " coffee ", "date_from": "2026-10-01", "date_to": "2026-10-31", "category": "Food", "currency": "USD",
    })
    assert response.status_code == 200
    assert ListStorage.calls == [EntryFilter(
        entry_type="income", search="coffee", date_from=date(2026, 10, 1), date_to=date(2026, 10, 31),
        category="Food", currency="USD",
    )]


@pytest.mark.parametrize("params", [
    {"date_from": "2026-10-31", "date_to": "2026-10-01"},
    {"page": 0},
    {"page_size": 101},
    {"date_from": "not-a-date"},
])
def test_list_rejects_bad_params(client, params):
    assert client.get("/api/v1/expense", params=params).status_code == 422


# --- Notion -------------------------------------------------------------------------

async def test_notion_date_filter_sort_and_cursor_loop():
    service = notion_with({"Type": {}})
    pages = iter([
        {"results": [], "next_cursor": "c1", "has_more": True},
        {"results": [], "next_cursor": None, "has_more": False},
    ])

    async def query(data_source_id, **body):
        service.client.data_sources.queries.append(body)
        return next(pages)

    service.client.data_sources.query = query
    await service.get_all(EntryFilter(entry_type="income", date_from=date(2026, 10, 1), date_to=date(2026, 10, 31)))

    first, second = service.client.data_sources.queries
    assert first["filter"] == {"and": [
        {"property": "Type", "select": {"equals": "income"}},
        {"property": "Date", "date": {"on_or_after": "2026-10-01"}},
        {"property": "Date", "date": {"on_or_before": "2026-10-31"}},
    ]}
    assert first["sorts"] == [{"property": "Date", "direction": "descending"}]
    assert "start_cursor" not in first and second["start_cursor"] == "c1"

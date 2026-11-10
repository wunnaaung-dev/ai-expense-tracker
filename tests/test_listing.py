from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from core.dependencies import (
    get_entry_service,
    get_insights_service,
    get_sheets_provisioning_service,
    get_sheets_storage,
    get_storage_service,
    get_storage_tag,
)
from core.security import get_current_user
from main import app
from schemas.expense import EntryFilter, ExpenseData
from schemas.storage import StorageStatusResponse
from services.insights.insights_service import InsightsService
from services.storage import record_cache
from services.storage.filters import matches
from services.storage.pagination import paginate
from tests.test_currency import notion_with
from tests.test_entries import FakeExtractor, entry_service
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

# A catalogue with every dimension varied: type, category, currency and date.
RECORDS = [
    ExpenseData(
        date=date(2026, 10, (i % 28) + 1),
        amount=100 + i,
        description=f"e{i}",
        category="Food" if i % 2 == 0 else "Transport",
        type="income" if i % 4 == 0 else "expense",
        currency="MMK" if i % 3 == 0 else "THB",
    )
    for i in range(120)
]


class ListStorage(RecordingStorage):
    calls = []

    async def get_all(self, filters):
        self.calls.append(filters)
        return list(RECORDS)


TAG = ("google_sheets", "sheet-1")
USER = SimpleNamespace(id=1, default_currency="THB", week_start="monday")


@pytest.fixture
def client():
    ListStorage.calls = []
    storage = ListStorage()  # one shared instance: writes must be visible to later requests
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_storage_service] = lambda: storage
    app.dependency_overrides[get_storage_tag] = lambda: TAG
    app.dependency_overrides[get_insights_service] = lambda: InsightsService(storage, USER, TAG)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_returns_page_body(client):
    # The catalogue holds 90 expenses and 30 income records.
    response = client.get("/api/v1/expense", params={"page_size": 50, "page": 2})
    assert response.status_code == 200
    body = response.json()
    assert {k: v for k, v in body.items() if k != "content"} == {
        "page": 2, "pageSize": 50, "totalElements": 90, "totalPages": 2, "first": False, "last": True,
    }
    assert len(body["content"]) == 40

    income = client.get("/api/v1/income", params={"page_size": 50})
    assert income.status_code == 200
    assert income.json()["totalElements"] == 30


def test_list_reads_storage_once_and_unfiltered(client):
    response = client.get("/api/v1/income", params={
        "search": " coffee ", "date_from": "2026-10-01", "date_to": "2026-10-31",
        "category": " Food ", "currency": "USD",
    })
    assert response.status_code == 200
    # The whole store is read once with no filters; the params are applied to that read.
    assert ListStorage.calls == [EntryFilter()]


@pytest.mark.parametrize("params", [
    {"date_from": "2026-10-31", "date_to": "2026-10-01"},
    {"page": 0},
    {"page_size": 101},
    {"date_from": "not-a-date"},
])
def test_list_rejects_bad_params(client, params):
    assert client.get("/api/v1/expense", params=params).status_code == 422


# --- record cache --------------------------------------------------------------------

def test_page_and_filter_changes_are_served_from_the_cache(client):
    assert client.get("/api/v1/expense").status_code == 200
    assert len(ListStorage.calls) == 1

    for params in (
        {"page": 2},
        {"page_size": 100},
        {"search": "e1"},
        {"category": "Food"},
        {"currency": "MMK"},
        {"date_from": "2026-10-01", "date_to": "2026-10-15"},
    ):
        assert client.get("/api/v1/expense", params=params).status_code == 200
    assert client.get("/api/v1/income").status_code == 200

    assert len(ListStorage.calls) == 1  # every one of those was served from the cache


def test_expense_income_and_dashboard_share_one_read(client):
    assert client.get("/api/v1/expense").status_code == 200
    assert client.get("/api/v1/income").status_code == 200
    assert client.get("/api/v1/dashboard").status_code == 200

    assert len(ListStorage.calls) == 1


def test_refresh_forces_a_read(client):
    assert client.get("/api/v1/expense").status_code == 200
    assert len(ListStorage.calls) == 1

    assert client.get("/api/v1/expense", params={"refresh": True}).status_code == 200
    assert len(ListStorage.calls) == 2

    assert client.get("/api/v1/expense").status_code == 200  # the refreshed read is cached again
    assert len(ListStorage.calls) == 2


def _reference_filters(query, entry_type):
    """The filters the endpoint must have applied, built the way the endpoint builds them."""
    kwargs = dict(query)
    for key in ("date_from", "date_to"):
        if key in kwargs:
            kwargs[key] = date.fromisoformat(kwargs[key])
    return EntryFilter(entry_type=entry_type, **kwargs)


@pytest.mark.parametrize("query, entry_type", [
    ({"search": "e1"}, "expense"),
    ({"search": "e1"}, "income"),
    ({"category": "food"}, "expense"),
    ({"category": "Transport"}, "expense"),
    ({"currency": "MMK"}, "income"),
    ({"currency": "THB"}, "income"),
    ({"date_from": "2026-10-10"}, "expense"),
    ({"date_to": "2026-10-10"}, "income"),
    ({"date_from": "2026-10-05", "date_to": "2026-10-15"}, "expense"),
])
def test_list_filters_match_the_reference(client, query, entry_type):
    path = "/api/v1/expense" if entry_type == "expense" else "/api/v1/income"
    response = client.get(path, params={**query, "page_size": 100})
    assert response.status_code == 200

    expected = [record for record in RECORDS if matches(record, _reference_filters(query, entry_type))]
    body = response.json()
    assert body["totalElements"] == len(expected)
    assert [row["date"] for row in body["content"]] == [
        record.date.isoformat() for record in expected[:100]
    ]
    assert ListStorage.calls == [EntryFilter()]  # still one unfiltered read


class StubSheets:
    """Stands in for GoogleSheetsStorage.repair on the repair route."""

    repairs = 0

    async def repair(self):
        type(self).repairs += 1


class StubProvisioning:
    async def get_status(self, user_id):
        return StorageStatusResponse(
            connected=True, provider="google_sheets", target_id="sheet-1", status="ready"
        )


def test_save_update_delete_and_repair_invalidate_the_list_cache(client):
    http = client
    # Same on_saved wiring as core/dependencies.get_entry_service.
    service, _, _ = entry_service(FakeExtractor(None), on_saved=lambda: record_cache.invalidate(USER.id))
    app.dependency_overrides[get_entry_service] = lambda: service

    http.get("/api/v1/expense")
    assert len(ListStorage.calls) == 1

    created = http.post("/api/v1/entries", json={
        "type": "expense", "amount": 100, "currency": "THB", "date": "2026-10-22",
        "category": "Food", "payment_channel": "Cash", "source": "manual",
    })
    assert created.status_code == 201
    record_id = created.json()["entry"]["id"]
    http.get("/api/v1/expense")
    assert len(ListStorage.calls) == 2  # save invalidated the cache

    assert http.patch(f"/api/v1/entries/{record_id}", json={"amount": 150}).status_code == 200
    http.get("/api/v1/expense")
    assert len(ListStorage.calls) == 3  # update invalidated it

    assert http.delete(f"/api/v1/entries/{record_id}").status_code == 204
    http.get("/api/v1/expense")
    assert len(ListStorage.calls) == 4  # delete invalidated it

    app.dependency_overrides[get_sheets_storage] = lambda: StubSheets()
    app.dependency_overrides[get_sheets_provisioning_service] = lambda: StubProvisioning()
    assert http.post("/api/v1/storage/repair").status_code == 200
    assert StubSheets.repairs == 1
    http.get("/api/v1/expense")
    assert len(ListStorage.calls) == 5  # repair invalidated it


def test_a_tag_change_is_a_cache_miss(client):
    """Switching storage (or re-provisioning) changes the tag, so the old read is not reused."""
    assert client.get("/api/v1/expense").status_code == 200
    assert len(ListStorage.calls) == 1

    app.dependency_overrides[get_storage_tag] = lambda: ("google_sheets", "sheet-2")

    assert client.get("/api/v1/expense").status_code == 200
    assert len(ListStorage.calls) == 2


# --- Notion ---------------------------------------------------------------------------

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

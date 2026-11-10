from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.dependencies import get_storage_service, get_storage_tag
from core.security import get_current_user
from main import app
from schemas.expense import EntryFilter, ExpenseData
from services.entries.entry_defaults import EntryDefaults
from services.extraction.gemini_extractor import _currency
from services.notion.notion_storage_service import NotionStorageService
from services.notion.property_config import build_properties, parse_notion_page
from tests.test_entries import FakeExtractor, entry_from, entry_service
from tests.test_expense_schema import RecordingStorage


def expense(description="lunch", currency=None):
    return ExpenseData(date=date(2026, 10, 1), amount=3500, description=description, currency=currency)


# --- schema / extraction --------------------------------------------------------

@pytest.mark.parametrize("code", ["THB", "MMK", "USD"])
def test_supported_currencies_accepted(code):
    assert expense(currency=code).currency == code


def test_unsupported_currency_rejected():
    with pytest.raises(ValidationError):
        expense(currency="EUR")


@pytest.mark.parametrize("raw, expected", [("MMK", "MMK"), (" mmk ", "MMK"), ("EUR", None), (None, None), (5, None)])
def test_gemini_currency_is_normalised(raw, expected):
    assert _currency(raw) == expected


# --- EntryDefaults --------------------------------------------------------------

def defaults(currency="THB", channel="Cash"):
    return EntryDefaults(SimpleNamespace(default_currency=currency, default_payment_channel=channel))


def test_detected_currency_is_kept():
    assert defaults(currency="USD").currency("MMK") == ("MMK", "detected")


@pytest.mark.parametrize("detected", [None, "EUR"])
def test_missing_currency_uses_default(detected):
    assert defaults(currency="USD").currency(detected) == ("USD", "default")


def test_detected_channel_is_kept():
    assert defaults(channel="Bank").payment_channel("Cash") == ("Cash", "detected")


@pytest.mark.parametrize("detected", [None, "KBZPay app"])
def test_missing_channel_uses_default(detected):
    assert defaults(channel="Bank").payment_channel(detected) == ("Bank", "default")


async def test_saving_never_changes_the_default_currency():
    # "Mohinga 3,500 Ks" -> MMK detected and saved; "Lunch 120" (no signal) still gets the setting.
    service, db, user = entry_service(FakeExtractor(expense("Mohinga", currency="MMK")), currency="THB")
    first = await service.extract_text("expense", "Mohinga 3,500 Ks")
    assert (first.draft.currency, first.draft.currency_source) == ("MMK", "detected")

    await service.save(entry_from(first.draft), RecordingStorage())
    assert (user.default_currency, db.commits) == ("THB", 0)

    service.text_extraction = FakeExtractor(expense("Lunch"))
    second = await service.extract_text("expense", "Lunch 120")
    assert (second.draft.currency, second.draft.currency_source) == ("THB", "default")


# --- Notion ---------------------------------------------------------------------

def test_notion_writes_currency_and_old_pages_read_as_thb():
    properties = build_properties(expense(currency="MMK"))
    assert properties["Currency"] == {"select": {"name": "MMK"}}

    page = {"properties": dict(properties)}
    assert parse_notion_page(page).currency == "MMK"

    del page["properties"]["Currency"]
    assert parse_notion_page(page).currency == "THB"
    page["properties"]["Currency"] = {"select": None}
    assert parse_notion_page(page).currency == "THB"


class FakeDataSources:
    def __init__(self, properties):
        self.properties = properties
        self.updates = []
        self.queries = []

    async def retrieve(self, data_source_id):
        return {"properties": dict(self.properties)}

    async def update(self, data_source_id, properties):
        self.updates.append(properties)
        self.properties.update(properties)

    async def query(self, data_source_id, **body):
        self.queries.append(body)
        return {"results": [], "next_cursor": None}


class FakePages:
    def __init__(self):
        self.created = []

    async def create(self, parent, properties):
        self.created.append(properties)


def notion_with(properties):
    service = NotionStorageService(token="t", database_id="db")
    service._data_source_id = "ds"
    service.client = SimpleNamespace(data_sources=FakeDataSources(properties), pages=FakePages())
    return service


async def test_notion_adds_missing_currency_property_on_write():
    service = notion_with({"Name": {}})
    await service.insert_many([expense(currency="USD")])
    assert list(service.client.data_sources.updates[0]) == ["Currency", "Type"]
    assert service.client.pages.created[0]["Currency"] == {"select": {"name": "USD"}}

    await service.insert_many([expense(currency="USD")])
    assert len(service.client.data_sources.updates) == 1


async def test_notion_currency_filter():
    service = notion_with({"Currency": {}})
    await service.get_all(EntryFilter(currency="MMK"))
    await service.get_all(EntryFilter(currency="THB"))
    mmk, thb = service.client.data_sources.queries
    assert mmk["filter"] == {"property": "Currency", "select": {"equals": "MMK"}}
    assert {"property": "Currency", "select": {"is_empty": True}} in thb["filter"]["or"]


async def test_notion_without_currency_property_is_all_thb():
    service = notion_with({"Name": {}})
    assert await service.get_all(EntryFilter(currency="USD")) == []
    await service.get_all(EntryFilter(currency="THB"))
    assert "filter" not in service.client.data_sources.queries[0]



async def test_notion_type_filter():
    service = notion_with({"Type": {}})
    await service.get_all(EntryFilter(entry_type="income"))
    await service.get_all(EntryFilter(entry_type="expense"))
    income, expense_query = service.client.data_sources.queries
    assert income["filter"] == {"property": "Type", "select": {"equals": "income"}}
    assert {"property": "Type", "select": {"is_empty": True}} in expense_query["filter"]["or"]


async def test_notion_without_type_property_is_all_expense():
    service = notion_with({"Name": {}})
    assert await service.get_all(EntryFilter(entry_type="income")) == []
    await service.get_all(EntryFilter(entry_type="expense"))
    assert "filter" not in service.client.data_sources.queries[0]


async def test_notion_combines_currency_and_type_filters():
    service = notion_with({"Currency": {}, "Type": {}})
    await service.get_all(EntryFilter(currency="MMK", entry_type="income"))
    assert service.client.data_sources.queries[0]["filter"] == {"and": [
        {"property": "Currency", "select": {"equals": "MMK"}},
        {"property": "Type", "select": {"equals": "income"}},
    ]}

# --- router ---------------------------------------------------------------------

def test_list_rejects_unknown_currency_filter():
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, default_currency="THB")
    app.dependency_overrides[get_storage_service] = RecordingStorage
    app.dependency_overrides[get_storage_tag] = lambda: ("google_sheets", "sheet-1")
    try:
        response = TestClient(app).get("/api/v1/expense", params={"currency": "EUR"})
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


class TypeRecordingStorage(RecordingStorage):
    calls = []

    async def get_all(self, filters):
        self.calls.append((filters.currency, filters.entry_type))
        return [
            ExpenseData(date=date(2026, 10, 1), amount=100, description="lunch", currency="THB", type="expense"),
            ExpenseData(date=date(2026, 10, 1), amount=200, description="salary", currency="THB", type="income"),
        ]


@pytest.mark.parametrize("path, entry_type", [
    ("/api/v1/expense", "expense"),
    ("/api/v1/income", "income"),
])
def test_list_routes_filter_by_type(path, entry_type):
    TypeRecordingStorage.calls = []
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, default_currency="THB")
    app.dependency_overrides[get_storage_service] = TypeRecordingStorage
    app.dependency_overrides[get_storage_tag] = lambda: ("google_sheets", "sheet-1")
    try:
        response = TestClient(app).get(path, params={"currency": "THB"})
        assert response.status_code == 200
        content = response.json()["content"]
        assert len(content) == 1 and content[0]["type"] == entry_type
        # One unfiltered read is shared by both routes; the type filter is applied to it.
        assert TypeRecordingStorage.calls == [(None, None)]
        assert TestClient(app).get(path, params={"currency": "EUR"}).status_code == 422
    finally:
        app.dependency_overrides.clear()

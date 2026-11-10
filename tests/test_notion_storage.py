from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from notion_client import APIResponseError
from notion_client.errors import APIErrorCode

from schemas.expense import EntryFilter
from services.notion.notion_storage_service import NotionStorageService
from services.storage.errors import RecordNotFoundError, StorageWriteError

# Every property the database is expected to have, so no backfill runs by accident.
PROPERTIES = {"Name": {}, "Amount": {}, "Date": {}, "Category": {}, "Currency": {}, "Type": {}}


def not_found() -> APIResponseError:
    return APIResponseError(APIErrorCode.ObjectNotFound, 404, "not found", httpx.Headers(), "")


def page(page_id, description="Lunch", amount=120, day=22, category="Food",
         currency="THB", entry_type="expense"):
    return {
        "id": page_id,
        "properties": {
            "Name": {"title": [{"text": {"content": description}}]},
            "Amount": {"number": amount},
            "Date": {"date": {"start": date(2026, 10, day).isoformat()}},
            "Category": {"select": {"name": category}},
            "Currency": {"select": {"name": currency}},
            "Type": {"select": {"name": entry_type}},
        },
    }


class FakePages:
    def __init__(self, store):
        self.store = store
        self.updates = []

    async def retrieve(self, page_id):
        if page_id not in self.store:
            raise not_found()
        return self.store[page_id]

    async def update(self, page_id, **kwargs):
        if page_id not in self.store:
            raise not_found()
        self.updates.append((page_id, kwargs))
        if "properties" in kwargs:
            self.store[page_id]["properties"].update(kwargs["properties"])
        if kwargs.get("in_trash"):
            return self.store.pop(page_id)  # a trashed page disappears from every query
        return self.store[page_id]


class FakeDataSources:
    def __init__(self, properties, store):
        self.properties = properties
        self.store = store
        self.updates = []
        self.queries = []

    async def retrieve(self, data_source_id):
        return {"properties": dict(self.properties)}

    async def update(self, data_source_id, properties):
        self.updates.append(properties)
        self.properties.update(properties)

    async def query(self, data_source_id, **body):
        self.queries.append(body)
        return {"results": list(self.store.values()), "next_cursor": None}


class FakeDatabases:
    async def retrieve(self, database_id):
        return {"data_sources": [{"id": "ds"}]}


def notion_with(properties=PROPERTIES, pages=()):
    store = {p["id"]: p for p in pages}
    service = NotionStorageService(token="t", database_id="db")
    service._data_source_id = "ds"
    service.client = SimpleNamespace(
        pages=FakePages(store),
        data_sources=FakeDataSources(properties, store),
        databases=FakeDatabases(),
    )
    return service


# --- get_one ----------------------------------------------------------------------

async def test_get_one_returns_the_record_and_its_page_id():
    service = notion_with(pages=[page("p-1")])

    record = await service.get_one("p-1")

    assert record.id == "p-1"
    assert (record.description, record.amount, record.date) == ("Lunch", 120, date(2026, 10, 22))
    assert (record.category, record.currency, record.type) == ("Food", "THB", "expense")


async def test_get_one_is_not_found_for_an_unknown_or_trashed_page():
    service = notion_with(pages=[page("p-1")])
    with pytest.raises(RecordNotFoundError):
        await service.get_one("nope")

    await service.delete("p-1")
    with pytest.raises(RecordNotFoundError):
        await service.get_one("p-1")


# --- update -----------------------------------------------------------------------

async def test_update_sends_only_the_changed_properties():
    service = notion_with(pages=[page("p-1")])

    await service.update("p-1", {"amount": 150, "category": "Transportation"})

    assert service.client.pages.updates == [("p-1", {"properties": {
        "Amount": {"number": 150},
        "Category": {"select": {"name": "Transportation"}},
    }})]
    assert service.client.data_sources.updates == []  # nothing was missing


async def test_update_rewrites_a_value_the_encoder_builds_from_the_merged_record():
    # The title encoder reads description, not amount: a partial patch must still
    # produce a complete value for whichever property it touches.
    service = notion_with(pages=[page("p-1", description="Lunch")])

    await service.update("p-1", {"description": "Brunch"})

    assert service.client.pages.updates[0][1]["properties"] == {
        "Name": {"title": [{"text": {"content": "Brunch"}}]}
    }


async def test_update_is_not_found_for_an_unknown_page():
    service = notion_with()
    with pytest.raises(RecordNotFoundError):
        await service.update("nope", {"amount": 1})
    assert service.client.pages.updates == []


async def test_update_with_nothing_sent_writes_nothing():
    service = notion_with(pages=[page("p-1")])
    await service.update("p-1", {})
    assert service.client.pages.updates == []


async def test_update_backfills_missing_properties_first():
    service = notion_with(properties={"Name": {}, "Amount": {}, "Date": {}, "Category": {}},
                          pages=[page("p-1")])

    await service.update("p-1", {"currency": "USD"})

    assert list(service.client.data_sources.updates[0]) == ["Currency", "Type"]
    assert service.client.pages.updates[0][1]["properties"] == {"Currency": {"select": {"name": "USD"}}}


async def test_update_reports_a_failed_write():
    service = notion_with(pages=[page("p-1")])

    async def explode(page_id, **kwargs):
        raise APIResponseError("validation_error", 400, "bad", httpx.Headers(), "")

    service.client.pages.update = explode
    with pytest.raises(StorageWriteError, match="bad"):
        await service.update("p-1", {"amount": 1})


# --- delete -----------------------------------------------------------------------

async def test_delete_trashes_the_page_so_reads_stop_returning_it():
    service = notion_with(pages=[page("p-1"), page("p-2", description="Taxi")])

    await service.delete("p-1")

    assert service.client.pages.updates == [("p-1", {"in_trash": True})]
    records = await service.get_all(EntryFilter())
    assert [record.description for record in records] == ["Taxi"]


async def test_delete_is_not_found_for_an_unknown_page():
    service = notion_with()
    with pytest.raises(RecordNotFoundError):
        await service.delete("nope")
    assert service.client.pages.updates == []

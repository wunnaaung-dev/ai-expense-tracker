from types import SimpleNamespace
from typing import List

import pytest
from fastapi.testclient import TestClient

from core.dependencies import get_storage_service
from core.security import get_current_user
from main import app
from schemas.expense import BatchResult, EntryFilter, ExpenseData
from services.notion.notion_storage_service import NotionStorageService
from services.storage.abstract_storage_strategy import StorageStrategy
from services.storage.errors import StorageWriteError

ITEM = ExpenseData(date="2026-10-19", amount=120, description="lunch")


class FailingStorage(StorageStrategy):
    async def insert_many(self, items: List[ExpenseData]) -> List[BatchResult]:
        raise StorageWriteError("backend down")

    async def get_all(self, filters: EntryFilter) -> List[ExpenseData]:
        return []

    async def get_one(self, record_id: str) -> ExpenseData:
        raise StorageWriteError("backend down")

    async def update(self, record_id: str, changes) -> None:
        raise StorageWriteError("backend down")

    async def delete(self, record_id: str) -> None:
        raise StorageWriteError("backend down")


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, default_currency="THB")
    app.dependency_overrides[get_storage_service] = FailingStorage
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_storage_write_failure_returns_502_with_code(client):
    response = client.post("/api/v1/entries", json={
        "type": "expense", "amount": 120, "currency": "THB", "date": "2026-10-19",
        "category": "Food", "payment_channel": "Cash", "source": "text",
    })
    assert response.status_code == 502
    assert response.json()["code"] == "storage_write_failed"


def test_sync_backup_endpoint_is_gone(client):
    assert client.post("/api/v1/expense/sync-backup").status_code == 404


def _notion_with_results(results):
    service = NotionStorageService.__new__(NotionStorageService)
    service._property_names = {"Currency", "Type"}

    async def fake_insert_one(item):
        return results.pop(0)

    service._insert_one = fake_insert_one
    return service


async def test_notion_all_failed_raises_storage_write_error():
    failed = BatchResult(filename="", status="failed", data=ITEM, error="boom")
    service = _notion_with_results([failed, failed])
    with pytest.raises(StorageWriteError):
        await service.insert_many([ITEM, ITEM])


async def test_notion_partial_failure_stays_per_item():
    ok = BatchResult(filename="", status="success", data=ITEM)
    failed = BatchResult(filename="", status="failed", data=ITEM, error="boom")
    service = _notion_with_results([ok, failed])
    results = await service.insert_many([ITEM, ITEM])
    assert [r.status for r in results] == ["success", "failed"]

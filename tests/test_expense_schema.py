from datetime import date
from typing import Any, Dict, List
import uuid

from schemas.expense import BatchResult, EntryFilter, ExpenseData
from services.storage.abstract_storage_strategy import StorageStrategy
from services.storage.errors import RecordNotFoundError
from services.storage.filters import matches


def test_legacy_extractor_payload_gets_defaults():
    # Same shape gemini_extractor builds: only the original four keys.
    item = ExpenseData(date="2026-10-19", amount=120.0, description="lunch", category="Food")
    assert item.date == date(2026, 10, 19)
    assert item.id is None
    assert item.type == "expense"
    assert item.payment_channel is None
    assert item.source is None
    assert item.currency is None


class RecordingStorage(StorageStrategy):
    """The real behaviour of a backend, in memory: ids, filtering and single-record edits."""

    def __init__(self):
        self.saved: List[ExpenseData] = []

    async def insert_many(self, items: List[ExpenseData]) -> List[BatchResult]:
        stored = [item.model_copy(update={"id": item.id or str(uuid.uuid4())}) for item in items]
        self.saved.extend(stored)
        return [BatchResult(filename="", status="success", data=i) for i in stored]

    async def get_all(self, filters: EntryFilter) -> List[ExpenseData]:
        return [record for record in self.saved if matches(record, filters)]

    async def get_one(self, record_id: str) -> ExpenseData:
        return self.saved[self._index_of(record_id)]

    async def update(self, record_id: str, changes: Dict[str, Any]) -> None:
        index = self._index_of(record_id)
        self.saved[index] = self.saved[index].model_copy(update=changes)

    async def delete(self, record_id: str) -> None:
        del self.saved[self._index_of(record_id)]

    def _index_of(self, record_id: str) -> int:
        for index, record in enumerate(self.saved):
            if record.id == record_id:
                return index
        raise RecordNotFoundError(record_id)


class FakeDb:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1

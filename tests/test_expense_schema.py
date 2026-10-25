from datetime import date
from typing import List

from schemas.expense import BatchResult, EntryFilter, ExpenseData
from services.storage.abstract_storage_strategy import StorageStrategy


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
    def __init__(self):
        self.saved: List[ExpenseData] = []

    async def insert_many(self, items: List[ExpenseData]) -> List[BatchResult]:
        self.saved.extend(items)
        return [BatchResult(filename="", status="success", data=i) for i in items]

    async def get_all(self, filters: EntryFilter) -> List[ExpenseData]:
        return []


class FakeDb:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1

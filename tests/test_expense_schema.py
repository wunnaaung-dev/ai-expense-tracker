from datetime import date
from typing import List, Optional, Tuple

from schemas.expense import BatchResult, ExpenseData
from services.extraction.abstract_text_extraction_strategy import TextExtractionStrategy
from services.storage.abstract_storage_strategy import StorageStrategy
from services.upload.text_expense_orchestrator import TextExpenseOrchestrator


def test_legacy_extractor_payload_gets_defaults():
    # Same shape gemini_extractor builds: only the original four keys.
    item = ExpenseData(date="2026-10-19", amount=120.0, description="lunch", category="Food")
    assert item.date == date(2026, 10, 19)
    assert item.id is None
    assert item.type == "expense"
    assert item.payment_channel is None
    assert item.source is None


class StubExtractor(TextExtractionStrategy):
    async def extract_from_text(self, text: str) -> List[ExpenseData]:
        return [ExpenseData(date="2026-10-19", amount=1, description="x")]


class RecordingStorage(StorageStrategy):
    def __init__(self):
        self.saved: List[ExpenseData] = []

    async def insert_many(self, items: List[ExpenseData]) -> List[BatchResult]:
        self.saved.extend(items)
        return [BatchResult(filename="", status="success", data=i) for i in items]

    async def get_all(self, page_size: int, start_cursor: Optional[str] = None) -> Tuple[List[ExpenseData], Optional[str]]:
        return [], None


async def test_text_orchestrator_tags_source():
    storage = RecordingStorage()
    await TextExpenseOrchestrator(extraction=StubExtractor(), storage=storage).process("x 1")
    assert [i.source for i in storage.saved] == ["text"]

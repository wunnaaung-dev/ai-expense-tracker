from typing import List

from schemas.expense import BatchResult, ExpenseData
from services.extraction.abstract_text_extraction_strategy import TextExtractionStrategy
from services.storage.abstract_storage_strategy import StorageStrategy

class TextExpenseOrchestrator:
    def __init__(self, extraction: TextExtractionStrategy, storage: StorageStrategy):
        self.extraction = extraction
        self.storage = storage

    async def process(self, text: str) -> List[BatchResult]:
        items = await self._extract(text)

        if not items:
            return [self._no_expense_found_result()]

        for item in items:
            item.source = "text"
        return await self.storage.insert_many(items)

    async def _extract(self, text: str) -> List[ExpenseData]:
        return await self.extraction.extract_from_text(text)

    @staticmethod
    def _no_expense_found_result() -> BatchResult:
        return BatchResult(
            filename="text-input",
            status="failed",
            error="No valid expense found in the text",
        )
from typing import List

from schemas.expense import BatchResult
from services.storage.abstract_storage_strategy import StorageStrategy
from services.extraction.abstract_audio_extraction_strategy import AudioExtractionStrategy


class VoiceExpenseOrchestrator:
    def __init__(self, extraction: AudioExtractionStrategy, storage: StorageStrategy):
        self.extraction = extraction
        self.storage = storage

    async def process(self, audio_bytes: bytes, mime_type: str) -> List[BatchResult]:
        items = await self.extraction.extract_from_audio(audio_bytes, mime_type)

        if not items:
            return [self._no_expense_found_result()]

        return await self.storage.insert_many(items)

    @staticmethod
    def _no_expense_found_result() -> BatchResult:
        return BatchResult(
            filename="voice-input",
            status="failed",
            error="No valid expense found in the recording",
        )
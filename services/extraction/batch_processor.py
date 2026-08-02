import asyncio
from fastapi import UploadFile

from schemas.expense import BatchResult
from services.extraction.abstract_image_extraction_strategy import ImageExtractionStrategy

MAX_CONCURRENT_REQUESTS = 3

class BatchExtractionProcessor:
    def __init__(self, strategy: ImageExtractionStrategy, max_concurrent: int = MAX_CONCURRENT_REQUESTS):
        self._strategy = strategy
        self._limiter = asyncio.Semaphore(max_concurrent)

    async def process_one(self, file: UploadFile) -> BatchResult:
        async with self._limiter:
            try:
                image_bytes = await file.read()
                data = await self._strategy.extract(image_bytes, file.content_type or "image/jpeg")
                return BatchResult(filename=file.filename or "unknown", status="success", data=data)
            except Exception as e:
                return BatchResult(filename=file.filename or "unknown", status="failed", error=str(e))

    async def process_batch(self, files: list[UploadFile]) -> list[BatchResult]:
        return await asyncio.gather(*[self.process_one(f) for f in files])
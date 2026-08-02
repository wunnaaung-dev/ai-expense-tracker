from typing import List, Dict

from fastapi import UploadFile

from schemas.expense import BatchResult, ExpenseData
from services.extraction.abstract_image_extraction_strategy import ImageExtractionStrategy
from services.storage.abstract_storage_strategy import StorageStrategy
from services.extraction.batch_processor import BatchExtractionProcessor
from services.dedup.hash_checker import DuplicateChecker

class ExpenseUploadOrchestrator:
    def __init__(
        self,
        image_extraction: ImageExtractionStrategy,
        storage: StorageStrategy,
        dedup: DuplicateChecker,
    ):
        self.extraction = image_extraction
        self.storage = storage
        self.dedup = dedup
    
    async def process(self, files: List[UploadFile]) -> List[BatchResult]:
        duplicate_results, files_to_process, file_hashes = await self._filter_deduplicate(files)
        
        results = await self._extract(files_to_process)
        await self._save_and_mark(results, file_hashes)
        
        return duplicate_results + results
    
    async def _filter_deduplicate(
        self, files: List[UploadFile]
    ) -> tuple[List[BatchResult], List[UploadFile], Dict[str, str]]:
        
        seen_in_request = set()
        files_to_process: List[UploadFile] = []
        duplicate_results: List[BatchResult] = []
        file_hashes: Dict[str, str] = {}
        
        for file in files:
            file_bytes = await file.read()
            file_hash = await self.dedup.compute_hash(file_bytes)
            await file.seek(0)

            filename = file.filename or "unknown"

            if file_hash in seen_in_request or await self.dedup.exists(file_hash):
                duplicate_results.append(
                    BatchResult(filename=filename, status="failed", error="Your file already exists")
                )
                continue

            seen_in_request.add(file_hash)
            files_to_process.append(file)
            file_hashes[filename] = file_hash

        return duplicate_results, files_to_process, file_hashes

    async def _extract(self, files: List[UploadFile]) -> List[BatchResult]:
        processor = BatchExtractionProcessor(self.extraction)
        return await processor.process_batch(files)
    
    async def _save_and_mark(self, results: List[BatchResult], file_hashes: Dict[str, str]) -> None:
        valid_items: List[ExpenseData] = [
            r.data for r in results if r.status == "success" and r.data is not None
        ]
        if not valid_items:
            return

        save_results = await self.storage.insert_many(valid_items)

        for i, r in enumerate(results):
            if r.status != "success" or i >= len(save_results):
                continue

            if save_results[i].status == "failed":
                r.status = "failed"
                r.error = save_results[i].error
            else:
                file_hash = file_hashes.get(r.filename or "unknown")
                if file_hash:
                    await self.dedup.mark_processed(file_hash, r.filename)
        
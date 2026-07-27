import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from services.notion.dependencies import get_storage_service
from services.notion.storage_strategy import StorageStrategy
from services.text_extraction.base import ExtractionService
from services.text_extraction.gemini_extractor import GeminiExtractor
from schemas.expense import BatchResult, ExpenseData

from typing import List


router = APIRouter()

MAX_FILES = 10
MAX_CONCURRENT_REQUESTS = 3
concurrency_limiter = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)


def get_extraction_service() -> ExtractionService:
    return GeminiExtractor()


async def process_one(file: UploadFile, service: ExtractionService) -> BatchResult:
    async with concurrency_limiter:
        try:
            image_bytes = await file.read()
            data = await service.extract(image_bytes, file.content_type or "image/jpeg")
            return BatchResult(filename=file.filename or "unknown", status="success", data=data)
        except Exception as e:
            return BatchResult(filename=file.filename or "unknown", status="failed", error=str(e))


@router.post("/expense/upload", response_model=list[BatchResult])
async def upload_expense_slips(
    files: list[UploadFile] = File(...),
    service: ExtractionService = Depends(get_extraction_service),
    storage: StorageStrategy = Depends(get_storage_service),
):
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Please don't send more than {MAX_FILES} slips per request (received {len(files)})",
        )

    results = await asyncio.gather(*[process_one(f, service) for f in files])

    valid_items = [
        r.data for r in results
        if r.status == "success" and r.data is not None
    ]
    if valid_items:
        save_results = await storage.insert_many(valid_items)
        for i, r in enumerate(results):
            if r.status == "success" and i < len(save_results) and save_results[i].status == "failed":
                r.status = "failed"
                r.error = save_results[i].error

    return results

@router.post("/expenses/save", response_model=List[BatchResult])
async def save_expenses(
    batch_results: List[BatchResult],
    storage: StorageStrategy = Depends(get_storage_service),
):
    valid_items: List[ExpenseData] = [
        result.data for result in batch_results
        if result.status == "success" and result.data is not None
    ]

    if not valid_items:
        return batch_results

    return await storage.insert_many(valid_items)
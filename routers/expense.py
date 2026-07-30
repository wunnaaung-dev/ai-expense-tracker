from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from services.notion.dependencies import get_storage_service
from services.notion.abstract_storage_strategy import StorageStrategy
from services.storage.resilient_storage_service import ResilientStorageService
from services.text_extraction.abstract_extraction_strategy import ExtractionStrategy
from services.text_extraction.batch_processor import BatchExtractionProcessor
from services.text_extraction.dependencies import get_extraction_service
from services.text_extraction.gemini_extractor import GeminiExtractor
from schemas.expense import BatchResult, ExpenseData

from typing import List

router = APIRouter()

MAX_FILES = 10

@router.post("/expense/upload", response_model=list[BatchResult])
async def upload_expense_slips(
    files: list[UploadFile] = File(...),
    service: ExtractionStrategy = Depends(get_extraction_service),
    storage: StorageStrategy = Depends(get_storage_service),
):
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Please don't send more than {MAX_FILES} slips per request (received {len(files)})",
        )

    processor = BatchExtractionProcessor(service)
    results = await processor.process_batch(files)  

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

@router.post("/expenses/sync-backup")
async def sync_backup(
    storage: ResilientStorageService = Depends(get_storage_service)
):
    results = await storage.sync_backup()
    return {
        "synced_count": len([r for r in results if r.status == "success"]),
        "failed_count": len([r for r in results if r.status == "failed"]),
        "results": results
    }
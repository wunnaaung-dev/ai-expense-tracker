import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from services.text_extraction.base import ExtractionService
from services.text_extraction.gemini_extractor import GeminiExtractor
from schemas.expense import BatchResult

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
):
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Please don't send more than {MAX_FILES} slips per request (received {len(files)})",
        )

    results = await asyncio.gather(*[process_one(f, service) for f in files])
    return results
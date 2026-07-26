from fastapi import APIRouter, UploadFile, Depends
from services.text_extraction.base import ExtractionService
from services.text_extraction.gemini_extractor import GeminiExtractor
from schemas.expense import ExpenseData

router = APIRouter()

def get_extraction_service() -> ExtractionService:
    return GeminiExtractor()

@router.post("/expense/upload", response_model=ExpenseData)
async def upload_expense_slip(
    file: UploadFile,
    service: ExtractionService = Depends(get_extraction_service)
):
    image_bytes = await file.read()
    return await service.extract(image_bytes, file.content_type or "image/jpeg")
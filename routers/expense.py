from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from core.dependencies import get_expense_upload_orchestrator, get_storage_service, get_text_expense_orchestrator, get_voice_expense_orchestrator

from core.security import get_current_user
from services.storage.abstract_storage_strategy import StorageStrategy
from schemas.expense import BatchResult, ExpenseListResponse, TextExpenseRequest

from typing import List, Optional

from services.upload.expense_upload_orchestrator import ExpenseUploadOrchestrator
from services.upload.text_expense_orchestrator import TextExpenseOrchestrator
from services.upload.voice_expense_orchestrator import VoiceExpenseOrchestrator

router = APIRouter(prefix="/api/v1", tags=["expense"], dependencies=[Depends(get_current_user)])

MAX_FILES = 10

@router.get("/expense", response_model=ExpenseListResponse)
async def get_all_expenses(
    page_size: int = Query(default=10, ge=1, le=100),
    start_cursor: Optional[str] = Query(default=None),
    storage: StorageStrategy = Depends(get_storage_service)
):
    expenses, next_cursor = await storage.get_all(page_size, start_cursor)
    return ExpenseListResponse(expenses=expenses, next_cursor=next_cursor)

ALLOWED_AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/wav", "audio/mp4", "audio/mpeg", "audio/x-m4a"}

@router.post("/expense/upload", response_model=list[BatchResult])
async def upload_expense_slips(
    files: list[UploadFile] = File(..., media_type="image/*"),
    orchestrator: ExpenseUploadOrchestrator = Depends(get_expense_upload_orchestrator)
):
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Please don't send more than {MAX_FILES} slips per request (received {len(files)})",
        )

    return await orchestrator.process(files)
    
@router.post("/expense/text", response_model=List[BatchResult])
async def log_expense_from_text(
    request: TextExpenseRequest,
    orchestrator: TextExpenseOrchestrator = Depends(get_text_expense_orchestrator),
):
    return await orchestrator.process(request.text)


@router.post("/expense/voice", response_model=list[BatchResult])
async def log_expense_from_voice(
    file: UploadFile = File(...),
    orchestrator: VoiceExpenseOrchestrator = Depends(get_voice_expense_orchestrator),
):
    if file.content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio type: {file.content_type}",
        )

    audio_bytes = await file.read()
    return await orchestrator.process(audio_bytes, file.content_type)
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from core.dependencies import get_expense_upload_orchestrator, get_storage_service, get_text_expense_orchestrator, get_voice_expense_orchestrator

from services.storage.resilient_storage_service import ResilientStorageService
from schemas.expense import BatchResult, TextExpenseRequest

from typing import List

from services.upload.expense_upload_orchestrator import ExpenseUploadOrchestrator
from services.upload.text_expense_orchestrator import TextExpenseOrchestrator
from services.upload.voice_expense_orchestrator import VoiceExpenseOrchestrator

router = APIRouter()

MAX_FILES = 10

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
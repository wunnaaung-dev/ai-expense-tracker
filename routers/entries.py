from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from core.dependencies import get_entry_service, get_storage_service
from core.security import get_current_user
from schemas.expense import EntryCreate, EntrySaveResponse, ExpenseType, ExtractMode, ExtractResponse
from services.entries.entry_service import EntryService
from services.storage.abstract_storage_strategy import StorageStrategy

router = APIRouter(prefix="/api/v1", tags=["entries"], dependencies=[Depends(get_current_user)])

ALLOWED_AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/wav", "audio/mp4", "audio/mpeg", "audio/x-m4a"}


@router.post("/entries/extract", response_model=ExtractResponse)
async def extract_entry(
    type: ExpenseType = Form(...),
    mode: ExtractMode = Form(...),
    text: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    duration_seconds: Optional[int] = Form(default=None, ge=0),
    service: EntryService = Depends(get_entry_service),
):
    """Read the input and return a draft for the review screen. Never writes to storage."""
    if mode == "text":
        if not text or not text.strip():
            raise HTTPException(status_code=400, detail="text is required for mode=text")
        return await service.extract_text(type, text)

    if file is None:
        raise HTTPException(status_code=400, detail=f"file is required for mode={mode}")
    content_type = file.content_type or ""
    if mode == "slip":
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"Unsupported image type: {content_type}")
        return await service.extract_slip(type, await file.read(), content_type, file.filename or "slip")

    if content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported audio type: {content_type}")
    return await service.extract_voice(type, await file.read(), content_type, duration_seconds)


@router.post("/entries", response_model=EntrySaveResponse, status_code=status.HTTP_201_CREATED)
async def save_entry(
    entry: EntryCreate,
    service: EntryService = Depends(get_entry_service),
    storage: StorageStrategy = Depends(get_storage_service),
):
    """Write the reviewed entry to the user's Google Sheet or Notion database."""
    return await service.save(entry, storage)

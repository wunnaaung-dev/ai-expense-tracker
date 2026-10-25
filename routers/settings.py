from fastapi import APIRouter, Depends

from core.dependencies import get_settings_service
from core.security import get_current_user
from schemas.settings import UserSettings, UserSettingsUpdate
from services.settings.settings_service import SettingsService

router = APIRouter(prefix="/api/v1", tags=["settings"], dependencies=[Depends(get_current_user)])


@router.get("/settings", response_model=UserSettings)
async def get_settings(service: SettingsService = Depends(get_settings_service)):
    return service.get()


@router.patch("/settings", response_model=UserSettings)
async def update_settings(
    body: UserSettingsUpdate,
    service: SettingsService = Depends(get_settings_service),
):
    """Change only the fields sent; returns the full settings."""
    return await service.update(body)

from fastapi import APIRouter, Depends, HTTPException, status

from core.dependencies import get_storage_config_service
from core.security import get_current_user
from models.users import User
from schemas.storage import NotionConnectRequest, StorageStatusResponse
from services.notion.notion_storage_service import NoSharedNotionPageError
from services.storage.storage_config_service import (
    InvalidNotionTokenError,
    NotionSetupError,
    StorageConfigService,
)

router = APIRouter(prefix="/api/v1", tags=["storage"], dependencies=[Depends(get_current_user)])


@router.get("/storage", response_model=StorageStatusResponse)
async def get_storage_status(
    current_user: User = Depends(get_current_user),
    service: StorageConfigService = Depends(get_storage_config_service),
):
    return await service.get_status(current_user.id)


@router.put("/storage/notion", response_model=StorageStatusResponse)
async def connect_notion(
    body: NotionConnectRequest,
    current_user: User = Depends(get_current_user),
    service: StorageConfigService = Depends(get_storage_config_service),
):
    try:
        return await service.connect_notion(current_user.id, body.token)
    except InvalidNotionTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Notion token",
        )
    except NoSharedNotionPageError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except NotionSetupError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.delete("/storage/notion", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_notion(
    current_user: User = Depends(get_current_user),
    service: StorageConfigService = Depends(get_storage_config_service),
):
    await service.disconnect_notion(current_user.id)

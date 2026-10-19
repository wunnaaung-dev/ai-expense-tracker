from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from constants.storage_status import STATUS_FAILED, STATUS_NEEDS_PERMISSION, STATUS_PROVISIONING
from core.dependencies import (
    get_sheets_provisioning_service,
    get_sheets_storage,
    get_storage_config_service,
)
from core.security import get_current_user
from models.users import User
from schemas.storage import NotionConnectRequest, StorageStatusResponse
from services.google_sheets.google_sheets_storage import GoogleSheetsStorage
from services.google_sheets.provisioning import SheetsProvisioningService
from services.notion.notion_storage_service import NoSharedNotionPageError
from services.storage.storage_config_service import (
    InvalidNotionTokenError,
    NotionSetupError,
    StorageConfigService,
)

router = APIRouter(prefix="/api/v1", tags=["storage"], dependencies=[Depends(get_current_user)])

_PROVISION_ERRORS = {
    STATUS_PROVISIONING: (status.HTTP_409_CONFLICT, "provisioning_in_progress"),
    STATUS_NEEDS_PERMISSION: (status.HTTP_403_FORBIDDEN, "needs_permission"),
    STATUS_FAILED: (status.HTTP_502_BAD_GATEWAY, "provisioning_failed"),
}


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


@router.get("/storage/status", response_model=StorageStatusResponse)
async def get_sheets_storage_status(
    current_user: User = Depends(get_current_user),
    service: SheetsProvisioningService = Depends(get_sheets_provisioning_service),
):
    return await service.get_status(current_user.id)


@router.post("/storage/provision", response_model=StorageStatusResponse)
async def provision_sheets_storage(
    current_user: User = Depends(get_current_user),
    service: SheetsProvisioningService = Depends(get_sheets_provisioning_service),
):
    # Also the "Re-create" action for failed or missing spreadsheets.
    result = await service.ensure_spreadsheet(current_user.id)
    if result in _PROVISION_ERRORS:
        status_code, code = _PROVISION_ERRORS[result]
        return JSONResponse(status_code=status_code, content={"code": code, "status": result})
    return await service.get_status(current_user.id)


@router.post("/storage/repair", response_model=StorageStatusResponse)
async def repair_sheets_storage(
    current_user: User = Depends(get_current_user),
    storage: GoogleSheetsStorage = Depends(get_sheets_storage),
    service: SheetsProvisioningService = Depends(get_sheets_provisioning_service),
):
    # Re-adds missing required columns (or the tab); errors map via core/errors.py.
    await storage.repair()
    return await service.get_status(current_user.id)

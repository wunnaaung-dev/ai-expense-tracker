from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from notion_client import APIResponseError
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import encrypt_secret
from core.db import get_db
from core.security import get_current_user
from models.storage import UserStorageConfig
from models.users import User
from schemas.storage import NotionConnectRequest, StorageStatusResponse
from services.notion.notion_storage_service import NotionStorageService
from services.storage.user_storage import NOTION_PROVIDER, get_notion_config

router = APIRouter(prefix="/api/v1", tags=["storage"], dependencies=[Depends(get_current_user)])


@router.get("/storage", response_model=StorageStatusResponse)
async def get_storage_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = await get_notion_config(db, current_user.id)
    if not config or not config.is_active:
        return StorageStatusResponse(connected=False)
    return StorageStatusResponse(
        connected=True,
        provider=config.provider,
        target_id=config.target_id,
        connected_at=config.connected_at,
    )


@router.put("/storage/notion", response_model=StorageStatusResponse)
async def connect_notion(
    body: NotionConnectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = NotionStorageService(token=body.token, database_id=body.database_id)
    try:
        await service.verify_access()
    except APIResponseError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Notion token or database ID",
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not access Notion database. Share it with the integration.",
        )

    encrypted = encrypt_secret(body.token)
    config = await get_notion_config(db, current_user.id)
    if config:
        config.encrypted_credentials = encrypted
        config.target_id = service.database_id
        config.is_active = True
        config.connected_at = datetime.now(timezone.utc)
    else:
        config = UserStorageConfig(
            user_id=current_user.id,
            provider=NOTION_PROVIDER,
            encrypted_credentials=encrypted,
            target_id=service.database_id,
            is_active=True,
        )
        db.add(config)

    await db.commit()
    await db.refresh(config)
    return StorageStatusResponse(
        connected=True,
        provider=config.provider,
        target_id=config.target_id,
        connected_at=config.connected_at,
    )


@router.delete("/storage/notion", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_notion(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = await get_notion_config(db, current_user.id)
    if config:
        await db.delete(config)
        await db.commit()

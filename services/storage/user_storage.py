from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from constants.storage_providers import NOTION_PROVIDER
from models.storage import UserStorageConfig


async def get_storage_config(
    db: AsyncSession,
    user_id: int,
    provider: str,
) -> UserStorageConfig | None:
    result = await db.execute(
        select(UserStorageConfig).where(
            UserStorageConfig.user_id == user_id,
            UserStorageConfig.provider == provider,
        )
    )
    return result.scalar_one_or_none()


async def get_notion_config(db: AsyncSession, user_id: int) -> UserStorageConfig | None:
    return await get_storage_config(db, user_id, NOTION_PROVIDER)


async def set_storage_status(db: AsyncSession, config: UserStorageConfig, status: str) -> None:
    if config.storage_status == status:
        return
    config.storage_status = status
    config.status_changed_at = datetime.now(timezone.utc)
    await db.commit()

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.storage import UserStorageConfig

NOTION_PROVIDER = "notion"


async def get_notion_config(db: AsyncSession, user_id: int) -> UserStorageConfig | None:
    result = await db.execute(
        select(UserStorageConfig).where(
            UserStorageConfig.user_id == user_id,
            UserStorageConfig.provider == NOTION_PROVIDER,
        )
    )
    return result.scalar_one_or_none()

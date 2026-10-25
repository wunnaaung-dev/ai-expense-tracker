from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from constants.storage_providers import NOTION_PROVIDER
from constants.storage_status import STATUS_PROVISIONING
from models.storage import UserStorageConfig

# A crashed request can't hold the claim forever: after this, another request may take over.
PROVISIONING_LEASE = timedelta(minutes=2)


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


async def reset_and_set_status(db: AsyncSession, config: UserStorageConfig, status: str) -> None:
    try:
        await set_storage_status(db, config, status)
    except Exception:
        # Only a DB error leaves the session unusable; roll back just then,
        # since rollback expires every loaded object (including the user).
        await db.rollback()
        await db.refresh(config)
        await set_storage_status(db, config, status)


async def claim_provisioning(db: AsyncSession, config: UserStorageConfig) -> bool:
    """Atomically move to `provisioning` unless a live claim exists (compare-and-set)."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(UserStorageConfig)
        .where(
            UserStorageConfig.id == config.id,
            or_(
                UserStorageConfig.storage_status != STATUS_PROVISIONING,
                UserStorageConfig.status_changed_at.is_(None),
                UserStorageConfig.status_changed_at < now - PROVISIONING_LEASE,
            ),
        )
        .values(storage_status=STATUS_PROVISIONING, status_changed_at=now)
        .returning(UserStorageConfig.id)
        .execution_options(synchronize_session=False)
    )
    claimed = result.scalar_one_or_none() is not None
    await db.commit()
    await db.refresh(config)
    return claimed

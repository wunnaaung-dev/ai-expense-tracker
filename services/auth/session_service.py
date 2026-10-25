import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import ACCESS_TOKEN_TTL, create_access_token
from models.sessions import UserSession
from services.auth.client_info import ClientInfo, describe_device, mask_ip


class SessionNotFoundError(Exception):
    """No active session with that id for this user."""


async def start_session(db: AsyncSession, user_id: int, client: Optional[ClientInfo]) -> str:
    """Record a new signed-in browser and return its JWT. Commits."""
    now = datetime.now(timezone.utc)
    # Housekeeping: ended sessions are never shown again.
    await db.execute(
        delete(UserSession).where(
            UserSession.user_id == user_id,
            or_(UserSession.revoked_at.is_not(None), UserSession.expires_at <= now),
        )
    )
    device, kind = describe_device(client.user_agent if client else None)
    session = UserSession(
        id=uuid.uuid4().hex,
        user_id=user_id,
        device=device,
        device_kind=kind,
        ip_masked=mask_ip(client.ip if client else None),
        created_at=now,
        last_seen_at=now,
        expires_at=now + ACCESS_TOKEN_TTL,
    )
    db.add(session)
    await db.commit()
    return create_access_token(user_id, session.id)


async def active_sessions(db: AsyncSession, user_id: int) -> List[UserSession]:
    result = await db.execute(
        select(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(timezone.utc),
        )
        .order_by(UserSession.last_seen_at.desc())
    )
    return list(result.scalars().all())


async def revoke_session(db: AsyncSession, user_id: int, session_id: str) -> None:
    result = await db.execute(
        update(UserSession)
        .where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    if result.rowcount == 0:
        raise SessionNotFoundError(session_id)
    await db.commit()


async def revoke_other_sessions(db: AsyncSession, user_id: int, current_id: str) -> int:
    result = await db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.id != current_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return result.rowcount

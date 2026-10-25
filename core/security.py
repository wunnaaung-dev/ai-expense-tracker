from datetime import datetime, timedelta, timezone

from authlib.jose import JoseError, jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import get_db
from models.sessions import UserSession
from models.users import User

ACCESS_TOKEN_COOKIE = "access_token"
ACCESS_TOKEN_TTL = timedelta(days=7)
ACCESS_TOKEN_MAX_AGE = int(ACCESS_TOKEN_TTL.total_seconds())
# last_seen_at is written at most this often, not on every request.
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)


def create_access_token(user_id: int, session_id: str) -> str:
    payload = {
        "user_id": user_id,
        "sid": session_id,
        "exp": datetime.now(timezone.utc) + ACCESS_TOKEN_TTL,
    }
    token = jwt.encode({"alg": "HS256"}, payload, settings.jwt_secret)
    if isinstance(token, bytes):
        return token.decode("utf-8")
    return token


def decode_access_token(token: str) -> dict | None:
    """The token's claims, or None when it is invalid or expired."""
    try:
        claims = jwt.decode(token, settings.jwt_secret)
        claims.validate()
    except JoseError:
        return None
    return dict(claims)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _as_utc(value: datetime) -> datetime:
    # SQLite hands back naive datetimes; Postgres aware ones.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        raise _unauthorized("Not authenticated")

    user_claims = decode_access_token(token)
    if user_claims is None:
        raise _unauthorized("Invalid or expired token")

    user_id = user_claims.get("user_id")
    session_id = user_claims.get("sid")
    # Tokens from before sessions existed have no sid: those users sign in again once.
    if user_id is None or session_id is None:
        raise _unauthorized("Invalid token payload")

    now = datetime.now(timezone.utc)
    session = (await db.execute(
        select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
    )).scalar_one_or_none()
    if session is None:
        raise _unauthorized("Session ended")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise _unauthorized("User not found")

    if now - _as_utc(session.last_seen_at) > SESSION_TOUCH_INTERVAL:
        session.last_seen_at = now
        await db.commit()
    request.state.session_id = session_id
    return user

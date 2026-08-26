import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import create_access_token
from models.users import User

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_google_user(self, user_info: dict) -> User:
        google_sub = user_info.get("sub")
        result = await self.db.execute(
            select(User).where(User.google_sub == google_sub)
        )
        user = result.scalar_one_or_none()
        if user:
            return user

        user = User(
            google_sub=google_sub,
            email=user_info.get("email"),
            name=user_info.get("name"),
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        logger.info("New user created: %s", user.id)
        return user

    async def login_with_google(self, user_info: dict) -> tuple[User, str]:
        user = await self.get_or_create_google_user(user_info)
        return user, create_access_token(user.id)

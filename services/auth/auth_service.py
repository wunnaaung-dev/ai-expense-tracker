import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from constants.storage_providers import GOOGLE_SHEETS_PROVIDER, NOTION_PROVIDER
from core.crypto import encrypt_credentials
from core.security import create_access_token
from models.storage import UserStorageConfig
from models.users import User
from services.storage.user_storage import get_notion_config

logger = logging.getLogger(__name__)


class NotionLoginError(Exception):
    """Raised when the Notion token response has no user owner."""


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_google_user(self, user_info: dict) -> User:
        google_sub = user_info.get("sub")
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(User).where(User.google_sub == google_sub)
        )
        user = result.scalar_one_or_none()
        if user:
            user.name = user_info.get("name")
            user.last_login_at = now
            if user_info.get("email"):
                user.email = user_info["email"]
            await self.db.commit()
            await self.db.refresh(user)
            return user

        user = User(
            google_sub=google_sub,
            email=user_info.get("email"),
            name=user_info.get("name"),
            default_storage_provider=GOOGLE_SHEETS_PROVIDER,
            last_login_at=now,
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        logger.info("New user created: %s", user.id)
        return user

    async def login_with_google(self, user_info: dict) -> tuple[User, str]:
        user = await self.get_or_create_google_user(user_info)
        return user, create_access_token(user.id)

    async def login_with_notion(self, token: dict) -> tuple[User, str]:
        owner = token.get("owner") or {}
        notion_user = owner.get("user") or {}
        notion_user_id = notion_user.get("id")
        if owner.get("type") != "user" or not notion_user_id:
            raise NotionLoginError("Notion login must be authorized by a user")

        user = await self._upsert_notion_user(notion_user_id, notion_user)
        await self._upsert_notion_credentials(user.id, token)
        await self.db.commit()
        await self.db.refresh(user)
        return user, create_access_token(user.id)

    async def _upsert_notion_user(self, notion_user_id: str, notion_user: dict) -> User:
        # Email is only present when the integration has the user-email capability.
        email = (notion_user.get("person") or {}).get("email")
        now = datetime.now(timezone.utc)

        result = await self.db.execute(
            select(User).where(User.notion_user_id == notion_user_id)
        )
        user = result.scalar_one_or_none()
        if user:
            user.name = notion_user.get("name")
            user.avatar_url = notion_user.get("avatar_url")
            user.last_login_at = now
            if email:
                user.email = email
            return user

        user = User(
            notion_user_id=notion_user_id,
            email=email,
            name=notion_user.get("name"),
            avatar_url=notion_user.get("avatar_url"),
            default_storage_provider=NOTION_PROVIDER,
            last_login_at=now,
        )
        self.db.add(user)
        await self.db.flush()
        logger.info("New Notion user created: %s", user.id)
        return user

    async def _upsert_notion_credentials(self, user_id: int, token: dict) -> None:
        credentials = {
            "access_token": token["access_token"],
            "bot_id": token.get("bot_id"),
            "workspace_id": token.get("workspace_id"),
            "workspace_name": token.get("workspace_name"),
        }
        if token.get("refresh_token"):
            credentials["refresh_token"] = token["refresh_token"]
        encrypted = encrypt_credentials(credentials)

        config = await get_notion_config(self.db, user_id)
        if config:
            # Keep target_id: the user's expense database stays the same.
            config.encrypted_credentials = encrypted
            config.connected_at = datetime.now(timezone.utc)
        else:
            self.db.add(UserStorageConfig(
                user_id=user_id,
                provider=NOTION_PROVIDER,
                encrypted_credentials=encrypted,
            ))

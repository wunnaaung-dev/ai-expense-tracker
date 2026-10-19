import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from constants.storage_providers import GOOGLE_SHEETS_PROVIDER, NOTION_PROVIDER
from core.crypto import decrypt_credentials, encrypt_credentials
from core.security import create_access_token
from models.storage import UserStorageConfig
from models.users import User
from services.google.credentials import has_sheets_scopes, merge_google_credentials
from services.storage.user_storage import get_notion_config, get_storage_config

logger = logging.getLogger(__name__)


class NotionLoginError(Exception):
    """Raised when the Notion token response has no user owner."""


class GoogleLoginError(Exception):
    """Raised when the Google token response has no user info."""


GOOGLE_LOGIN_OK = "ok"
GOOGLE_LOGIN_NEEDS_PERMISSION = "needs_permission"
# No refresh token from Google and none stored: the caller should re-run consent once.
GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN = "needs_refresh_token"


@dataclass
class GoogleLoginResult:
    user: User
    access_token: str
    outcome: str


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_google_user(self, user_info: dict) -> User:
        google_sub = user_info.get("sub")
        now = datetime.now(timezone.utc)
        user = await self._find_google_user(google_sub)
        if user is None:
            user = User(
                google_sub=google_sub,
                email=user_info.get("email"),
                name=user_info.get("name"),
                default_storage_provider=GOOGLE_SHEETS_PROVIDER,
                last_login_at=now,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(user)
                logger.info("New user created: %s", user.id)
            except IntegrityError:
                # A concurrent first login created the same user.
                user = await self._find_google_user(google_sub)

        user.name = user_info.get("name")
        user.last_login_at = now
        if user_info.get("email"):
            user.email = user_info["email"]
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def _find_google_user(self, google_sub: str | None) -> User | None:
        result = await self.db.execute(
            select(User).where(User.google_sub == google_sub)
        )
        return result.scalar_one_or_none()

    async def login_with_google(self, token: dict) -> GoogleLoginResult:
        user_info = token.get("userinfo")
        if not user_info:
            raise GoogleLoginError("Google user info missing")

        user = await self.get_or_create_google_user(user_info)
        credentials = await self._upsert_google_credentials(user.id, token)
        jwt_token = create_access_token(user.id)

        if not credentials.get("refresh_token"):
            outcome = GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN
        elif not has_sheets_scopes(credentials.get("scope")):
            outcome = GOOGLE_LOGIN_NEEDS_PERMISSION
        else:
            outcome = GOOGLE_LOGIN_OK
        return GoogleLoginResult(user=user, access_token=jwt_token, outcome=outcome)

    async def _upsert_google_credentials(self, user_id: int, token: dict) -> dict:
        config = await get_storage_config(self.db, user_id, GOOGLE_SHEETS_PROVIDER)
        if config is None:
            credentials = merge_google_credentials(None, token)
            try:
                async with self.db.begin_nested():
                    self.db.add(UserStorageConfig(
                        user_id=user_id,
                        provider=GOOGLE_SHEETS_PROVIDER,
                        encrypted_credentials=encrypt_credentials(credentials),
                    ))
                await self.db.commit()
                return credentials
            except IntegrityError:
                # A concurrent login inserted the row first; merge into it below.
                config = await get_storage_config(self.db, user_id, GOOGLE_SHEETS_PROVIDER)

        # Keep target_id: the user's spreadsheet stays the same.
        old = decrypt_credentials(config.encrypted_credentials)
        credentials = merge_google_credentials(old, token)
        config.encrypted_credentials = encrypt_credentials(credentials)
        await self.db.commit()
        return credentials

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

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from constants.storage_providers import GOOGLE_SHEETS_PROVIDER, NOTION_PROVIDER
from constants.storage_status import STATUS_FAILED, STATUS_NEEDS_PERMISSION
from core.crypto import decrypt_credentials, encrypt_credentials
from models.sessions import UserSession
from models.storage import ExpensesDedup, UserStorageConfig
from models.users import User
from services.google.credentials import has_sheets_scopes, merge_google_credentials, revoke_google_token
from services.auth.client_info import ClientInfo
from services.auth.session_service import start_session
from services.google_sheets.provisioning import SheetsProvisioningService
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
GOOGLE_LOGIN_PROVISIONING_FAILED = "provisioning_failed"

CONNECT_OK = "ok"
# The Google / Notion identity already belongs to another account; accounts are never merged.
CONNECT_ACCOUNT_IN_USE = "account_in_use"


@dataclass
class GoogleLoginResult:
    user: User
    access_token: str
    outcome: str


class AuthService:
    def __init__(self, db: AsyncSession, provisioning: SheetsProvisioningService | None = None):
        self.db = db
        self.provisioning = provisioning

    async def delete_account(self, user: User) -> None:
        """Delete the user and everything the server keeps for them. Their Sheet / Notion data is untouched."""
        await self._revoke_google_access(user.id)
        # Explicit deletes (not only the FK cascade) so every database removes the same rows.
        await self.db.execute(delete(ExpensesDedup).where(ExpensesDedup.user_id == user.id))
        await self.db.execute(delete(UserSession).where(UserSession.user_id == user.id))
        await self.db.execute(delete(UserStorageConfig).where(UserStorageConfig.user_id == user.id))
        await self.db.delete(user)
        await self.db.commit()

    async def _revoke_google_access(self, user_id: int) -> None:
        """Best effort: a failed revoke never blocks deleting the account."""
        config = await get_storage_config(self.db, user_id, GOOGLE_SHEETS_PROVIDER)
        if config is None or not config.encrypted_credentials:
            return
        try:
            credentials = decrypt_credentials(config.encrypted_credentials)
            token = credentials.get("refresh_token") or credentials.get("access_token")
            if token:
                await revoke_google_token(token)
        except Exception:
            logger.warning("Google token revoke failed for user %s", user_id, exc_info=True)

    async def get_or_create_google_user(self, user_info: dict) -> User:
        google_sub = user_info.get("sub")
        now = datetime.now(timezone.utc)
        user = await self._find_google_user(google_sub)
        if user is None:
            user = User(
                google_sub=google_sub,
                email=user_info.get("email"),
                name=user_info.get("name"),
                avatar_url=user_info.get("picture"),
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
        user.avatar_url = user_info.get("picture")
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

    async def login_with_google(self, token: dict, client: ClientInfo | None = None) -> GoogleLoginResult:
        user_info = token.get("userinfo")
        if not user_info:
            raise GoogleLoginError("Google user info missing")

        user = await self.get_or_create_google_user(user_info)
        credentials = await self._upsert_google_credentials(user.id, token)
        jwt_token = await start_session(self.db, user.id, client)

        if not credentials.get("refresh_token"):
            # The caller re-runs consent; provision on that return trip.
            outcome = GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN
        else:
            outcome = await self._provision_storage(user.id, credentials)
        return GoogleLoginResult(user=user, access_token=jwt_token, outcome=outcome)

    async def _provision_storage(self, user_id: int, credentials: dict) -> str:
        if self.provisioning is None:
            return GOOGLE_LOGIN_OK if has_sheets_scopes(credentials.get("scope")) else GOOGLE_LOGIN_NEEDS_PERMISSION
        # A storage problem must never fail the login itself.
        try:
            status = await self.provisioning.ensure_spreadsheet(user_id)
        except Exception:
            logger.exception("Unexpected provisioning error for user %s", user_id)
            return GOOGLE_LOGIN_PROVISIONING_FAILED
        if status == STATUS_NEEDS_PERMISSION:
            return GOOGLE_LOGIN_NEEDS_PERMISSION
        if status == STATUS_FAILED:
            return GOOGLE_LOGIN_PROVISIONING_FAILED
        # ready, schema_broken, or another request is provisioning: the frontend reads /storage/status.
        return GOOGLE_LOGIN_OK

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

    async def login_with_notion(self, token: dict, client: ClientInfo | None = None) -> tuple[User, str]:
        owner = token.get("owner") or {}
        notion_user = owner.get("user") or {}
        notion_user_id = notion_user.get("id")
        if owner.get("type") != "user" or not notion_user_id:
            raise NotionLoginError("Notion login must be authorized by a user")

        user = await self._upsert_notion_user(notion_user_id, notion_user)
        await self._upsert_notion_credentials(user.id, token)
        await self.db.commit()
        await self.db.refresh(user)
        return user, await start_session(self.db, user.id, client)

    async def connect_google(self, user: User, token: dict) -> str:
        """Link a Google identity to a signed-in user and set up their spreadsheet. Returns the outcome."""
        user_info = token.get("userinfo")
        if not user_info or not user_info.get("sub"):
            raise GoogleLoginError("Google user info missing")

        google_sub = user_info["sub"]
        if user.google_sub != google_sub:
            owner = await self._find_google_user(google_sub)
            if user.google_sub or owner is not None:
                return CONNECT_ACCOUNT_IN_USE
            if not await self._link_identity(user, google_sub=google_sub):
                return CONNECT_ACCOUNT_IN_USE

        credentials = await self._upsert_google_credentials(user.id, token)
        if not credentials.get("refresh_token"):
            return GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN
        return await self._provision_storage(user.id, credentials)

    async def connect_notion(self, user: User, token: dict) -> str:
        """Link a Notion identity to a signed-in user. The database is set up afterwards via /storage/notion/setup."""
        notion_user_id = self._notion_owner_id(token)
        if user.notion_user_id != notion_user_id:
            result = await self.db.execute(select(User).where(User.notion_user_id == notion_user_id))
            if user.notion_user_id or result.scalar_one_or_none() is not None:
                return CONNECT_ACCOUNT_IN_USE
            if not await self._link_identity(user, notion_user_id=notion_user_id):
                return CONNECT_ACCOUNT_IN_USE

        await self._upsert_notion_credentials(user.id, token)
        await self.db.commit()
        return CONNECT_OK

    async def _link_identity(self, user: User, **identity: str) -> bool:
        """Set google_sub / notion_user_id; False when a concurrent request took it first."""
        try:
            async with self.db.begin_nested():
                for column, value in identity.items():
                    setattr(user, column, value)
        except IntegrityError:
            await self.db.refresh(user)
            return False
        await self.db.commit()
        return True

    @staticmethod
    def _notion_owner_id(token: dict) -> str:
        owner = token.get("owner") or {}
        notion_user_id = (owner.get("user") or {}).get("id")
        if owner.get("type") != "user" or not notion_user_id:
            raise NotionLoginError("Notion login must be authorized by a user")
        return notion_user_id

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

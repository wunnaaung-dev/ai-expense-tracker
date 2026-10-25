from sqlalchemy.ext.asyncio import AsyncSession

from models.users import User
from schemas.settings import UserSettings, UserSettingsUpdate


class SettingsService:
    """The user's entry settings (Settings → Currency & entry defaults)."""

    def __init__(self, db: AsyncSession, user: User):
        self.db = db
        self.user = user

    def get(self) -> UserSettings:
        return UserSettings.model_validate(self.user)

    async def update(self, changes: UserSettingsUpdate) -> UserSettings:
        changed = {
            field: value
            for field, value in changes.model_dump(exclude_unset=True).items()
            if getattr(self.user, field) != value
        }
        if changed:
            for field, value in changed.items():
                setattr(self.user, field, value)
            await self.db.commit()
        return self.get()

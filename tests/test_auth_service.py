from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from models.users import User
from services.auth.auth_service import AuthService

GOOGLE_USER = {"sub": "google-123", "email": "a@example.com", "name": "A"}


async def test_new_google_user_gets_last_login_at(db):
    user = await AuthService(db).get_or_create_google_user(GOOGLE_USER)
    assert user.last_login_at is not None
    assert user.default_storage_provider == "google_sheets"


async def test_returning_google_user_updates_last_login_at_and_profile(db):
    service = AuthService(db)
    user = await service.get_or_create_google_user(GOOGLE_USER)
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    user.last_login_at = old
    await db.commit()

    again = await service.get_or_create_google_user({**GOOGLE_USER, "name": "A2", "email": "b@example.com"})

    assert again.id == user.id
    assert again.name == "A2"
    assert again.email == "b@example.com"
    assert again.last_login_at.replace(tzinfo=timezone.utc) > old + timedelta(days=1)
    rows = (await db.execute(select(User))).scalars().all()
    assert len(rows) == 1

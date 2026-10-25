from sqlalchemy import func, select

from constants.storage_providers import GOOGLE_SHEETS_PROVIDER
from core.crypto import encrypt_credentials
from models.sessions import UserSession
from models.storage import ExpensesDedup, UserStorageConfig
from models.users import User
from services.auth import auth_service as auth_module
from services.auth.auth_service import AuthService
from services.auth.session_service import start_session

GOOGLE_USER = {"sub": "google-123", "email": "a@example.com", "name": "A", "picture": None}


async def user_with_data(db, credentials=None):
    user = await AuthService(db).get_or_create_google_user(GOOGLE_USER)
    db.add(UserStorageConfig(
        user_id=user.id,
        provider=GOOGLE_SHEETS_PROVIDER,
        encrypted_credentials=encrypt_credentials(credentials or {"access_token": "at", "refresh_token": "rt"}),
        target_id="sheet-1",
        storage_status="ready",
    ))
    db.add(ExpensesDedup(user_id=user.id, hash="h1"))
    await db.commit()
    await start_session(db, user.id, None)
    return user


async def count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def test_delete_account_removes_everything_and_revokes_google(db, monkeypatch):
    revoked = []

    async def fake_revoke(token, http_client=None):
        revoked.append(token)

    monkeypatch.setattr(auth_module, "revoke_google_token", fake_revoke)
    user = await user_with_data(db)

    await AuthService(db).delete_account(user)

    assert revoked == ["rt"]
    assert (
        await count(db, User), await count(db, UserStorageConfig), await count(db, ExpensesDedup), await count(db, UserSession)
    ) == (0, 0, 0, 0)


async def test_failed_revoke_still_deletes(db, monkeypatch):
    async def failing_revoke(token, http_client=None):
        raise RuntimeError("google down")

    monkeypatch.setattr(auth_module, "revoke_google_token", failing_revoke)
    user = await user_with_data(db)

    await AuthService(db).delete_account(user)

    assert await count(db, User) == 0


async def test_delete_only_touches_that_user(db, monkeypatch):
    async def fake_revoke(token, http_client=None):
        pass

    monkeypatch.setattr(auth_module, "revoke_google_token", fake_revoke)
    user = await user_with_data(db)
    other = await AuthService(db).get_or_create_google_user({**GOOGLE_USER, "sub": "google-456"})
    db.add(ExpensesDedup(user_id=other.id, hash="h2"))
    await db.commit()

    await AuthService(db).delete_account(user)

    assert (await count(db, User), await count(db, ExpensesDedup)) == (1, 1)


def test_delete_route_clears_the_cookie():
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from core.dependencies import get_auth_service
    from core.security import get_current_user
    from main import app

    deleted = []

    class FakeAuthService:
        async def delete_account(self, user):
            deleted.append(user.id)

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)
    app.dependency_overrides[get_auth_service] = FakeAuthService
    try:
        client = TestClient(app)
        client.cookies.set("access_token", "x")
        response = client.delete("/api/auth/me")
        assert response.status_code == 204
        assert deleted == [7]
        assert 'access_token=""' in response.headers["set-cookie"]
    finally:
        app.dependency_overrides.clear()

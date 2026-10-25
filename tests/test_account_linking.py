from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from sqlalchemy import select

from constants.storage_status import STATUS_FAILED, STATUS_NONE, STATUS_READY
from core.db import get_db
from core.dependencies import get_auth_service
from core.oauth import oauth
from core.security import ACCESS_TOKEN_COOKIE
from main import app
from models.storage import UserStorageConfig
from models.users import User
from services.auth.auth_service import (
    CONNECT_ACCOUNT_IN_USE,
    CONNECT_OK,
    GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN,
    GOOGLE_LOGIN_OK,
    AuthService,
)
from services.auth.session_service import start_session
from services.storage.storage_config_service import StorageConfigService
from services.storage.errors import StorageNotReadyError
from services.storage.user_storage import get_storage_config
from tests.fake_google import FakeGoogle
from tests.test_google_login import google_token
from tests.test_notion_provisioning import notion_token
from tests.test_provisioning import provisioning_for


def other_notion_token(user_id: str = "notion-user-2") -> dict:
    token = notion_token()
    token["owner"] = {"type": "user", "user": {"id": user_id, "name": "Other"}}
    return token


async def notion_user(db) -> User:
    user, _ = await AuthService(db).login_with_notion(notion_token())
    return user


async def google_user(db) -> User:
    return (await AuthService(db).login_with_google(google_token())).user


async def configs(db, user_id: int) -> dict[str, UserStorageConfig]:
    rows = (await db.execute(select(UserStorageConfig).where(UserStorageConfig.user_id == user_id))).scalars()
    return {row.provider: row for row in rows}


# --- connect: service ---------------------------------------------------------------------

async def test_notion_user_connects_google_and_gets_a_spreadsheet(db):
    user = await notion_user(db)
    fake = FakeGoogle()

    outcome = await AuthService(db, provisioning_for(db, fake)).connect_google(user, google_token())

    assert outcome == GOOGLE_LOGIN_OK
    await db.refresh(user)
    assert user.google_sub == "g-1"
    assert user.default_storage_provider == "notion"
    sheets = (await configs(db, user.id))["google_sheets"]
    assert sheets.storage_status == STATUS_READY
    assert fake.create_calls == 1


async def test_linked_google_login_reaches_the_same_account(db):
    user = await notion_user(db)
    await AuthService(db).connect_google(user, google_token())

    result = await AuthService(db).login_with_google(google_token())

    assert result.user.id == user.id
    assert (await db.execute(select(User))).scalars().all() == [result.user]


async def test_google_user_connects_notion(db):
    user = await google_user(db)

    assert await AuthService(db).connect_notion(user, notion_token()) == CONNECT_OK

    await db.refresh(user)
    assert user.notion_user_id == "notion-user-1"
    assert user.default_storage_provider == "google_sheets"
    assert (await configs(db, user.id))["notion"].storage_status == STATUS_NONE
    # Signing in with Notion now reaches this account.
    same, _ = await AuthService(db).login_with_notion(notion_token())
    assert same.id == user.id


async def test_identity_owned_by_another_account_is_refused(db):
    owner = await google_user(db)
    user = await notion_user(db)

    assert await AuthService(db).connect_google(user, google_token()) == CONNECT_ACCOUNT_IN_USE
    await db.refresh(user)
    assert user.google_sub is None
    assert "google_sheets" not in await configs(db, user.id)

    assert await AuthService(db).connect_notion(owner, notion_token()) == CONNECT_ACCOUNT_IN_USE
    await db.refresh(owner)
    assert owner.notion_user_id is None


async def test_a_different_identity_of_the_same_provider_is_refused(db):
    user = await notion_user(db)
    assert await AuthService(db).connect_notion(user, other_notion_token()) == CONNECT_ACCOUNT_IN_USE
    await db.refresh(user)
    assert user.notion_user_id == "notion-user-1"


async def test_reconnecting_the_same_identity_refreshes_credentials(db):
    user = await google_user(db)
    assert await AuthService(db).connect_google(user, google_token(access_token="at2")) == GOOGLE_LOGIN_OK


async def test_connect_google_without_refresh_token_asks_for_consent(db):
    user = await notion_user(db)
    token = google_token()
    del token["refresh_token"]

    assert await AuthService(db).connect_google(user, token) == GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN


# --- connect: routes ----------------------------------------------------------------------

@pytest.fixture
async def http(db, monkeypatch):
    async def override_db():
        yield db

    async def fake_redirect(request, redirect_uri, **kwargs):
        return RedirectResponse("https://provider.example/authorize")

    tokens = {"google": google_token(), "notion": notion_token()}

    def exchange(name):
        async def fake_exchange(request):
            return tokens[name]
        return fake_exchange

    for name in ("google", "notion"):
        client = getattr(oauth, name)
        monkeypatch.setattr(client, "authorize_redirect", fake_redirect)
        monkeypatch.setattr(client, "authorize_access_token", exchange(name))

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_service] = lambda: AuthService(db)
    client = TestClient(app, follow_redirects=False)
    client.tokens = tokens
    yield client
    app.dependency_overrides.clear()


def settings_result(response) -> tuple[str, str]:
    parsed = urlparse(response.headers["location"])
    assert parsed.path == "/settings"
    query = parse_qs(parsed.query)
    return query["connect"][0], query["status"][0]


async def sign_in(db, http, user: User) -> str:
    token = await start_session(db, user.id, None)
    http.cookies.set(ACCESS_TOKEN_COOKIE, token)
    return token


async def test_connect_route_links_and_keeps_the_session(db, http):
    user = await google_user(db)
    token = await sign_in(db, http, user)

    assert http.get("/api/auth/notion/connect").status_code == 307
    response = http.get("/api/auth/notion/callback")

    assert settings_result(response) == ("notion", "ok")
    assert "access_token" not in response.headers.get("set-cookie", "")
    await db.refresh(user)
    assert user.notion_user_id == "notion-user-1"
    assert http.cookies.get(ACCESS_TOKEN_COOKIE) == token


async def test_connect_route_reports_account_in_use(db, http):
    await notion_user(db)
    user = await google_user(db)
    await sign_in(db, http, user)

    http.get("/api/auth/notion/connect")
    assert settings_result(http.get("/api/auth/notion/callback")) == ("notion", "account_in_use")


async def test_connect_requires_sign_in(http):
    assert http.get("/api/auth/google/connect").status_code == 401


async def test_connect_callback_for_another_signed_in_user_is_an_error(db, http):
    user = await notion_user(db)
    await sign_in(db, http, user)
    http.get("/api/auth/google/connect")

    other = await google_user(db)
    await sign_in(db, http, other)
    response = http.get("/api/auth/google/callback")

    assert settings_result(response) == ("google", "error")
    await db.refresh(user)
    assert user.google_sub is None


async def test_plain_login_after_abandoned_connect_signs_in(db, http):
    user = await google_user(db)
    await sign_in(db, http, user)
    http.get("/api/auth/notion/connect")

    http.get("/api/auth/notion/login")
    response = http.get("/api/auth/notion/callback")

    assert urlparse(response.headers["location"]).path == "/auth/callback"
    assert "access_token=" in response.headers["set-cookie"]


async def test_google_connect_without_refresh_token_bounces_to_consent_once(db, http):
    user = await notion_user(db)
    await sign_in(db, http, user)
    del http.tokens["google"]["refresh_token"]

    http.get("/api/auth/google/connect")
    first = http.get("/api/auth/google/callback")
    assert first.headers["location"] == "https://provider.example/authorize"

    second = http.get("/api/auth/google/callback")
    assert settings_result(second) == ("google", "needs_permission")


# --- switch -------------------------------------------------------------------------------

async def test_switch_to_a_ready_provider(db):
    user = await notion_user(db)
    await AuthService(db, provisioning_for(db, FakeGoogle())).connect_google(user, google_token())

    result = await StorageConfigService(db).set_default_provider(user, "google_sheets")

    assert result.default_storage_provider == "google_sheets"
    assert result.storage.provider == "google_sheets"
    assert result.storage.connected is True
    await db.refresh(user)
    assert user.default_storage_provider == "google_sheets"


async def test_switch_to_a_provider_that_is_not_ready_is_refused(db):
    user = await google_user(db)
    await AuthService(db).connect_notion(user, notion_token())

    with pytest.raises(StorageNotReadyError) as error:
        await StorageConfigService(db).set_default_provider(user, "notion")

    assert error.value.status == STATUS_NONE
    await db.refresh(user)
    assert user.default_storage_provider == "google_sheets"


async def test_switch_to_an_unconnected_or_failed_provider_is_refused(db):
    user = await notion_user(db)
    with pytest.raises(StorageNotReadyError) as error:
        await StorageConfigService(db).set_default_provider(user, "google_sheets")
    assert error.value.status == STATUS_NONE

    await AuthService(db, provisioning_for(db, FakeGoogle())).connect_google(user, google_token())
    config = await get_storage_config(db, user.id, "google_sheets")
    config.storage_status = STATUS_FAILED
    await db.commit()
    with pytest.raises(StorageNotReadyError) as error:
        await StorageConfigService(db).set_default_provider(user, "google_sheets")
    assert error.value.status == STATUS_FAILED


async def test_switch_route(db, http):
    user = await notion_user(db)
    await AuthService(db, provisioning_for(db, FakeGoogle())).connect_google(user, google_token())
    await sign_in(db, http, user)

    response = http.put("/api/v1/storage/default", json={"provider": "google_sheets"})
    assert response.status_code == 200
    assert response.json()["default_storage_provider"] == "google_sheets"

    # Back to Notion: it has no database yet.
    response = http.put("/api/v1/storage/default", json={"provider": "notion"})
    assert response.status_code == 409
    assert response.json() == {"code": "storage_not_ready", "status": STATUS_NONE}

    assert http.put("/api/v1/storage/default", json={"provider": "dropbox"}).status_code == 422
    assert http.get("/api/auth/me").json()["default_storage_provider"] == "google_sheets"

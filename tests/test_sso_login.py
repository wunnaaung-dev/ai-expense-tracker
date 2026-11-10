from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from sqlalchemy import select

from core.db import get_db
from core.dependencies import get_auth_service
from core.oauth import oauth
from main import app
from models.sessions import UserSession
from models.storage import UserStorageConfig
from models.users import User
from services.auth.auth_service import AccountExistsOtherProvider, AuthService
from tests.test_google_login import USERINFO, google_token
from tests.test_notion_provisioning import notion_token

EMAIL = "a@example.com"


def verified_google_token(**userinfo) -> dict:
    return google_token(userinfo={**USERINFO, "email_verified": True, **userinfo})


def notion_token_with_email(email: str | None = EMAIL) -> dict:
    token = notion_token()
    owner_user = dict(token["owner"]["user"])
    if email is not None:
        owner_user["person"] = {"email": email}
    token["owner"] = {**token["owner"], "user": owner_user}
    return token


async def count(db, model) -> int:
    return len((await db.execute(select(model))).scalars().all())


# --- AuthService: first login with the other provider --------------------------------------

async def test_google_login_on_a_notion_account_email_is_refused(db):
    await AuthService(db).login_with_notion(notion_token_with_email())

    with pytest.raises(AccountExistsOtherProvider) as exc:
        await AuthService(db).login_with_google(verified_google_token(email="A@Example.com"))

    assert exc.value.provider == "notion"
    assert await count(db, User) == 1
    assert "google_sheets" not in {c.provider for c in (await db.execute(select(UserStorageConfig))).scalars()}
    assert await count(db, UserSession) == 1  # only the Notion login's


async def test_notion_login_on_a_google_account_email_is_refused(db):
    await AuthService(db).login_with_google(verified_google_token())

    with pytest.raises(AccountExistsOtherProvider) as exc:
        await AuthService(db).login_with_notion(notion_token_with_email())

    assert exc.value.provider == "google"
    assert await count(db, User) == 1


async def test_unverified_google_email_signs_up_as_before(db):
    await AuthService(db).login_with_notion(notion_token_with_email())

    result = await AuthService(db).login_with_google(google_token())  # no email_verified

    assert result.user.google_sub == "g-1"
    assert await count(db, User) == 2


async def test_notion_without_email_signs_up_as_before(db):
    await AuthService(db).login_with_google(verified_google_token())

    user, _ = await AuthService(db).login_with_notion(notion_token_with_email(None))

    assert user.notion_user_id == "notion-user-1"
    assert await count(db, User) == 2


async def test_returning_user_signs_in_as_before(db):
    first = (await AuthService(db).login_with_google(verified_google_token())).user
    again = (await AuthService(db).login_with_google(verified_google_token())).user
    assert again.id == first.id


async def test_user_with_both_providers_linked_signs_in_with_either(db):
    user = (await AuthService(db).login_with_google(verified_google_token())).user
    await AuthService(db).connect_notion(user, notion_token_with_email())

    same, _ = await AuthService(db).login_with_notion(notion_token_with_email())
    assert same.id == user.id
    assert (await AuthService(db).login_with_google(verified_google_token())).user.id == user.id


# --- Routes ------------------------------------------------------------------------------

@pytest.fixture
def http(db, monkeypatch):
    async def override_db():
        yield db

    async def fake_redirect(request, redirect_uri, **kwargs):
        return RedirectResponse("https://provider.example/authorize")

    tokens = {"google": verified_google_token(), "notion": notion_token_with_email()}

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
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.clear()


def callback_query(response) -> dict[str, str]:
    parsed = urlparse(response.headers["location"])
    assert parsed.path == "/auth/callback"
    return {key: values[0] for key, values in parse_qs(parsed.query).items()}


@pytest.mark.parametrize("path", ["/api/v1/auth/google/start", "/api/v1/auth/notion/start"])
def test_start_routes_redirect_to_the_provider(http, path):
    response = http.get(path)
    assert response.headers["location"] == "https://provider.example/authorize"


@pytest.mark.parametrize("path", ["/api/auth/google/login", "/api/auth/notion/login"])
def test_old_login_routes_still_work(http, path):
    assert http.get(path).headers["location"] == "https://provider.example/authorize"


async def test_google_callback_points_to_notion_without_a_cookie(db, http):
    await AuthService(db).login_with_notion(notion_token_with_email())

    response = http.get("/api/auth/google/callback")

    assert callback_query(response) == {"status": "account_exists_other_provider", "provider": "notion"}
    assert "access_token" not in response.headers.get("set-cookie", "")


async def test_notion_callback_points_to_google_without_a_cookie(db, http):
    await AuthService(db).login_with_google(verified_google_token())

    response = http.get("/api/auth/notion/callback")

    assert callback_query(response) == {"status": "account_exists_other_provider", "provider": "google"}
    assert "access_token" not in response.headers.get("set-cookie", "")

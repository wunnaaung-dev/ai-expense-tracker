from urllib.parse import parse_qs, urlparse

import pytest
from authlib.integrations.base_client import OAuthError
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from sqlalchemy import select

from core.crypto import decrypt_credentials
from core.dependencies import get_auth_service
from core.oauth import oauth
from main import app
from models.storage import UserStorageConfig
from services.auth.auth_service import (
    GOOGLE_LOGIN_NEEDS_PERMISSION,
    GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN,
    GOOGLE_LOGIN_OK,
    AuthService,
    GoogleLoginResult,
)
from services.google.credentials import DRIVE_FILE_SCOPE

USERINFO = {"sub": "g-1", "email": "a@example.com", "name": "A"}


def google_token(**overrides):
    token = {
        "access_token": "at",
        "expires_in": 3600,
        "scope": f"openid email profile {DRIVE_FILE_SCOPE}",
        "refresh_token": "rt",
        "userinfo": USERINFO,
    }
    token.update(overrides)
    return token


# --- AuthService -----------------------------------------------------------

async def test_login_stores_encrypted_credentials(db):
    result = await AuthService(db).login_with_google(google_token())
    assert result.outcome == GOOGLE_LOGIN_OK
    config = (await db.execute(select(UserStorageConfig))).scalar_one()
    assert config.provider == "google_sheets"
    assert "rt" not in config.encrypted_credentials
    assert decrypt_credentials(config.encrypted_credentials)["refresh_token"] == "rt"


async def test_relogin_without_refresh_token_keeps_stored_one(db):
    service = AuthService(db)
    await service.login_with_google(google_token())
    token = google_token(access_token="at2")
    del token["refresh_token"]

    result = await service.login_with_google(token)

    assert result.outcome == GOOGLE_LOGIN_OK
    config = (await db.execute(select(UserStorageConfig))).scalar_one()
    stored = decrypt_credentials(config.encrypted_credentials)
    assert stored["access_token"] == "at2"
    assert stored["refresh_token"] == "rt"


async def test_missing_drive_scope_needs_permission(db):
    result = await AuthService(db).login_with_google(google_token(scope="openid email profile"))
    assert result.outcome == GOOGLE_LOGIN_NEEDS_PERMISSION


async def test_no_refresh_token_anywhere_asks_for_reconsent(db):
    token = google_token()
    del token["refresh_token"]
    result = await AuthService(db).login_with_google(token)
    assert result.outcome == GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN


# --- Router ----------------------------------------------------------------

class StubAuthService:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    async def login_with_google(self, token):
        return GoogleLoginResult(user=None, access_token="jwt-value", outcome=self.outcomes.pop(0))


@pytest.fixture
def make_client(monkeypatch):
    async def fake_exchange(request):
        return google_token()

    monkeypatch.setattr(oauth.google, "authorize_access_token", fake_exchange)

    def make(outcomes):
        stub = StubAuthService(outcomes)
        app.dependency_overrides[get_auth_service] = lambda: stub
        return TestClient(app, follow_redirects=False)

    yield make
    app.dependency_overrides.clear()


def _frontend_status(response):
    location = response.headers["location"]
    assert "jwt-value" not in location
    parsed = urlparse(location)
    assert parsed.path == "/auth/callback"
    return parse_qs(parsed.query)["status"][0]


def test_callback_redirects_to_frontend_with_cookie(make_client):
    response = make_client([GOOGLE_LOGIN_OK]).get("/api/auth/google/callback")
    assert response.status_code == 302
    assert _frontend_status(response) == "ok"
    cookie = response.headers["set-cookie"]
    assert "access_token=jwt-value" in cookie
    assert "httponly" in cookie.lower()


def test_callback_needs_permission(make_client):
    response = make_client([GOOGLE_LOGIN_NEEDS_PERMISSION]).get("/api/auth/google/callback")
    assert _frontend_status(response) == "needs_permission"


def test_callback_oauth_error_redirects_without_cookie(make_client, monkeypatch):
    async def broken(request):
        raise OAuthError(error="access_denied")

    client = make_client([])
    monkeypatch.setattr(oauth.google, "authorize_access_token", broken)
    response = client.get("/api/auth/google/callback")
    assert _frontend_status(response) == "error"
    assert "access_token" not in response.headers.get("set-cookie", "")


def test_missing_refresh_token_bounces_to_consent_once(make_client):
    client = make_client([GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN, GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN])

    first = client.get("/api/auth/google/callback")
    assert first.status_code == 302
    assert first.headers["location"].endswith("/api/auth/google/login?reconsent=1")

    # Google still sent no refresh token: stop bouncing and report it.
    second = client.get("/api/auth/google/callback")
    assert _frontend_status(second) == "needs_permission"
    assert "access_token=jwt-value" in second.headers["set-cookie"]


def test_reconsent_login_adds_prompt_consent(make_client, monkeypatch):
    captured = []

    async def fake_redirect(request, redirect_uri, **kwargs):
        captured.append(kwargs)
        return RedirectResponse("https://accounts.google.com/o/oauth2/auth")

    monkeypatch.setattr(oauth.google, "authorize_redirect", fake_redirect)
    client = make_client([])
    client.get("/api/auth/google/login")
    client.get("/api/auth/google/login?reconsent=1")
    assert "prompt" not in captured[0]
    assert captured[1]["prompt"] == "consent"


def test_google_client_requests_offline_drive_file_only():
    assert oauth.google.authorize_params["access_type"] == "offline"
    scope = oauth.google.client_kwargs["scope"]
    assert DRIVE_FILE_SCOPE in scope
    assert "auth/spreadsheets" not in scope

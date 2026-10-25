from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from core.db import get_db
from core.security import ACCESS_TOKEN_COOKIE, create_access_token, decode_access_token, get_current_user
from main import app
from models.sessions import UserSession
from services.auth.auth_service import AuthService
from services.auth.client_info import ClientInfo, describe_device, mask_ip
from services.auth.session_service import (
    SessionNotFoundError,
    active_sessions,
    revoke_other_sessions,
    revoke_session,
    start_session,
)

CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
SAFARI_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
EDGE_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36 Edg/141.0"
FIREFOX_ANDROID = "Mozilla/5.0 (Android 14; Mobile; rv:131.0) Gecko/131.0 Firefox/131.0"

GOOGLE_USER = {"sub": "google-123", "email": "a@example.com", "name": "A", "picture": None}


# --- client info ----------------------------------------------------------------------

@pytest.mark.parametrize("ua, expected", [
    (CHROME_WIN, ("Chrome on Windows", "desktop")),
    (SAFARI_IPHONE, ("Safari on iPhone", "phone")),
    (EDGE_MAC, ("Edge on macOS", "desktop")),
    (FIREFOX_ANDROID, ("Firefox on Android", "phone")),
    ("curl/8.0", ("Unknown browser", "desktop")),
    (None, ("Unknown browser", "desktop")),
])
def test_describe_device(ua, expected):
    assert describe_device(ua) == expected


@pytest.mark.parametrize("ip, expected", [
    ("203.81.64.12", "203.81.x.x"),
    ("2001:0db8:85a3::1", "2001:db8:…"),
    ("not-an-ip", None),
    (None, None),
])
def test_mask_ip(ip, expected):
    assert mask_ip(ip) == expected


# --- service ----------------------------------------------------------------------------

async def new_user(db, sub="google-123"):
    return await AuthService(db).get_or_create_google_user({**GOOGLE_USER, "sub": sub})


def request_with(token: str | None) -> Request:
    headers = [(b"cookie", f"{ACCESS_TOKEN_COOKIE}={token}".encode())] if token else []
    return Request({"type": "http", "headers": headers})


async def test_login_starts_a_session_the_token_points_to(db):
    user = await new_user(db)
    token = await start_session(db, user.id, ClientInfo(user_agent=CHROME_WIN, ip="203.81.64.12"))

    sid = decode_access_token(token)["sid"]
    (session,) = await active_sessions(db, user.id)
    assert (session.id, session.device, session.ip_masked) == (sid, "Chrome on Windows", "203.81.x.x")
    request = request_with(token)
    assert (await get_current_user(request, db)).id == user.id
    assert request.state.session_id == sid


async def test_google_login_records_the_device(db):
    token = {"access_token": "at", "expires_in": 3600, "refresh_token": "rt", "scope": "", "userinfo": GOOGLE_USER}
    result = await AuthService(db).login_with_google(token, ClientInfo(user_agent=SAFARI_IPHONE, ip="1.2.3.4"))
    (session,) = await active_sessions(db, result.user.id)
    assert session.device == "Safari on iPhone"
    assert decode_access_token(result.access_token)["sid"] == session.id


async def test_revoked_expired_and_old_tokens_are_rejected(db):
    user = await new_user(db)
    revoked = await start_session(db, user.id, None)
    await revoke_session(db, user.id, decode_access_token(revoked)["sid"])

    expired = await start_session(db, user.id, None)
    session = await db.get(UserSession, decode_access_token(expired)["sid"])
    session.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.commit()

    old = create_access_token(user.id, "no-such-session")

    for token in (revoked, expired, old):
        with pytest.raises(HTTPException) as exc:
            await get_current_user(request_with(token), db)
        assert exc.value.status_code == 401


async def test_revoke_only_touches_own_sessions(db):
    user, other = await new_user(db), await new_user(db, "google-456")
    other_token = await start_session(db, other.id, None)
    with pytest.raises(SessionNotFoundError):
        await revoke_session(db, user.id, decode_access_token(other_token)["sid"])
    assert len(await active_sessions(db, other.id)) == 1


async def test_revoke_others_keeps_the_current_session(db):
    user = await new_user(db)
    current = decode_access_token(await start_session(db, user.id, None))["sid"]
    await start_session(db, user.id, None)
    await start_session(db, user.id, None)

    assert await revoke_other_sessions(db, user.id, current) == 2
    assert [s.id for s in await active_sessions(db, user.id)] == [current]


async def test_new_login_clears_ended_sessions(db):
    user = await new_user(db)
    first = decode_access_token(await start_session(db, user.id, None))["sid"]
    await revoke_session(db, user.id, first)
    await start_session(db, user.id, None)
    rows = (await db.execute(select(UserSession).where(UserSession.user_id == user.id))).scalars().all()
    assert len(rows) == 1


# --- routes -----------------------------------------------------------------------------

@pytest.fixture
async def http(db):
    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app)
    app.dependency_overrides.clear()


async def test_session_routes(db, http):
    user = await new_user(db)
    other_token = await start_session(db, user.id, ClientInfo(user_agent=SAFARI_IPHONE, ip="1.2.3.4"))
    token = await start_session(db, user.id, ClientInfo(user_agent=CHROME_WIN, ip="203.81.64.12"))
    current, other = decode_access_token(token)["sid"], decode_access_token(other_token)["sid"]
    http.cookies.set(ACCESS_TOKEN_COOKIE, token)

    body = http.get("/api/auth/sessions").json()
    assert [(s["id"], s["current"], s["device"]) for s in body] == [
        (current, True, "Chrome on Windows"),
        (other, False, "Safari on iPhone"),
    ]
    assert body[0]["ip_masked"] == "203.81.x.x"

    assert http.delete(f"/api/auth/sessions/{current}").status_code == 400
    assert http.delete("/api/auth/sessions/unknown").status_code == 404
    assert http.delete(f"/api/auth/sessions/{other}").status_code == 204
    assert [s["id"] for s in http.get("/api/auth/sessions").json()] == [current]
    assert http.post("/api/auth/sessions/revoke-others").json() == {"revoked": 0}


async def test_logout_ends_the_session(db, http):
    user = await new_user(db)
    token = await start_session(db, user.id, None)
    http.cookies.set(ACCESS_TOKEN_COOKIE, token)

    assert http.post("/api/auth/logout").status_code == 204
    assert await active_sessions(db, user.id) == []

    # The old cookie no longer works, and logging out without one is still fine.
    fresh = TestClient(app)
    fresh.cookies.set(ACCESS_TOKEN_COOKIE, token)
    assert fresh.get("/api/auth/me").status_code == 401
    assert TestClient(app).post("/api/auth/logout").status_code == 204

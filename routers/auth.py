from urllib.parse import urlencode

from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from constants.storage_providers import GOOGLE_SHEETS_PROVIDER, NOTION_PROVIDER
from core.config import settings
from core.cookies import _cookie_kwargs, set_auth_cookie
from core.db import get_db
from core.dependencies import get_auth_service
from core.oauth import oauth
from core.security import ACCESS_TOKEN_COOKIE, decode_access_token, get_current_user
from models.users import User
from schemas.sessions import SessionOut
from services.auth.auth_service import (
    GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN,
    AccountExistsOtherProvider,
    AuthService,
    GoogleLoginError,
    NotionLoginError,
)
from services.auth.client_info import client_info
from services.auth.session_service import (
    SessionNotFoundError,
    active_sessions,
    revoke_other_sessions,
    revoke_session,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _frontend_redirect(**params: str) -> RedirectResponse:
    url = f"{settings.frontend_url.rstrip('/')}/auth/callback?{urlencode(params)}"
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


def _settings_redirect(provider: str, outcome: str) -> RedirectResponse:
    query = urlencode({"connect": provider, "status": outcome})
    url = f"{settings.frontend_url.rstrip('/')}/settings?{query}"
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


# A first login whose email belongs to an account of the other provider: no account, no cookie;
# `provider` names the one to use (google | notion).
ACCOUNT_EXISTS_OTHER_PROVIDER = "account_exists_other_provider"

# Set while a consent re-prompt is in flight, so a missing refresh token can't loop.
_GOOGLE_RECONSENT_FLAG = "google_reconsent_attempted"
# Set by /{provider}/connect: the shared callback links the identity to this user instead of signing in.
_LINK_INTENT = "link"


def _start_link(request: Request, provider: str, user: User) -> None:
    request.session[_LINK_INTENT] = {"provider": provider, "user_id": user.id}


async def _linking_user(request: Request, db: AsyncSession, provider: str) -> tuple[bool, User | None]:
    """(is a connect flow, the signed-in user it was started by, or None if that no longer holds)."""
    intent = request.session.get(_LINK_INTENT)
    if not intent or intent.get("provider") != provider:
        return False, None
    request.session.pop(_LINK_INTENT)
    try:
        user = await get_current_user(request, db)
    except HTTPException:
        return True, None
    return True, user if user.id == intent.get("user_id") else None


async def _start_google_login(request: Request, reconsent: bool):
    # A plain sign-in must never finish as a leftover connect.
    request.session.pop(_LINK_INTENT, None)
    # Google only returns a refresh token on the consent screen.
    params = {"prompt": "consent"} if reconsent else {}
    return await oauth.google.authorize_redirect(request, settings.redirect_uri, **params)


async def _start_notion_login(request: Request):
    request.session.pop(_LINK_INTENT, None)
    return await oauth.notion.authorize_redirect(request, settings.notion_redirect_uri)


# The login start the frontend links to. Callbacks stay under /api/auth (the registered redirect URIs).
v1_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@v1_router.get("/google/start")
async def google_start(request: Request, reconsent: bool = False):
    return await _start_google_login(request, reconsent)


@v1_router.get("/notion/start")
async def notion_start(request: Request):
    return await _start_notion_login(request)


# Older alias of /api/v1/auth/google/start; the consent re-prompt below still goes through it.
@router.get("/google/login")
async def login(request: Request, reconsent: bool = False):
    return await _start_google_login(request, reconsent)


@router.get("/google/connect")
async def google_connect(request: Request, current_user: User = Depends(get_current_user)):
    """Link Google (and its spreadsheet) to the signed-in account. Browser navigation from Settings."""
    _start_link(request, GOOGLE_SHEETS_PROVIDER, current_user)
    return await oauth.google.authorize_redirect(request, settings.redirect_uri, prompt="consent")


@router.get("/google/callback")
async def callback(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_db),
):
    is_link, link_user = await _linking_user(request, db, GOOGLE_SHEETS_PROVIDER)
    if is_link:
        return await _google_connect_callback(request, auth_service, link_user)

    reconsent_attempted = request.session.pop(_GOOGLE_RECONSENT_FLAG, False)
    try:
        token = await oauth.google.authorize_access_token(request)
        result = await auth_service.login_with_google(token, client_info(request))
    except AccountExistsOtherProvider as exc:
        return _frontend_redirect(status=ACCOUNT_EXISTS_OTHER_PROVIDER, provider=exc.provider)
    except (OAuthError, GoogleLoginError):
        return _frontend_redirect(status="error")

    outcome = result.outcome
    if outcome == GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN:
        if not reconsent_attempted:
            request.session[_GOOGLE_RECONSENT_FLAG] = True
            login_url = request.url_for("login").include_query_params(reconsent=1)
            return RedirectResponse(str(login_url), status_code=status.HTTP_302_FOUND)
        outcome = "needs_permission"

    response = _frontend_redirect(status=outcome)
    set_auth_cookie(response, result.access_token)
    return response


async def _google_connect_callback(
    request: Request,
    auth_service: AuthService,
    user: User | None,
) -> RedirectResponse:
    reconsent_attempted = request.session.pop(_GOOGLE_RECONSENT_FLAG, False)
    if user is None:
        return _settings_redirect("google", "error")
    try:
        token = await oauth.google.authorize_access_token(request)
        outcome = await auth_service.connect_google(user, token)
    except (OAuthError, GoogleLoginError):
        return _settings_redirect("google", "error")

    if outcome == GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN:
        if not reconsent_attempted:
            request.session[_GOOGLE_RECONSENT_FLAG] = True
            _start_link(request, GOOGLE_SHEETS_PROVIDER, user)
            return await oauth.google.authorize_redirect(request, settings.redirect_uri, prompt="consent")
        outcome = "needs_permission"
    return _settings_redirect("google", outcome)


# Older alias of /api/v1/auth/notion/start
@router.get("/notion/login")
async def notion_login(request: Request):
    return await _start_notion_login(request)


@router.get("/notion/connect")
async def notion_connect(request: Request, current_user: User = Depends(get_current_user)):
    """Link Notion to the signed-in account; the database is created later via /storage/notion/setup."""
    _start_link(request, NOTION_PROVIDER, current_user)
    return await oauth.notion.authorize_redirect(request, settings.notion_redirect_uri)


@router.get("/notion/callback")
async def notion_callback(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_db),
):
    is_link, link_user = await _linking_user(request, db, NOTION_PROVIDER)
    if is_link:
        if link_user is None:
            return _settings_redirect("notion", "error")
        try:
            token = await oauth.notion.authorize_access_token(request)
            outcome = await auth_service.connect_notion(link_user, token)
        except (OAuthError, NotionLoginError):
            return _settings_redirect("notion", "error")
        return _settings_redirect("notion", outcome)

    try:
        token = await oauth.notion.authorize_access_token(request)
        _, jwt_token = await auth_service.login_with_notion(token, client_info(request))
    except AccountExistsOtherProvider as exc:
        return _frontend_redirect(status=ACCOUNT_EXISTS_OTHER_PROVIDER, provider=exc.provider)
    except OAuthError as exc:
        return _frontend_redirect(error=exc.error or "oauth_error")
    except NotionLoginError:
        return _frontend_redirect(error="invalid_notion_owner")

    response = _frontend_redirect(status="success")
    set_auth_cookie(response, jwt_token)
    return response


@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    # Best effort: end this browser's session too, so a copied cookie stops working.
    claims = decode_access_token(request.cookies.get(ACCESS_TOKEN_COOKIE) or "")
    if claims and claims.get("user_id") is not None and claims.get("sid"):
        try:
            await revoke_session(db, claims["user_id"], claims["sid"])
        except SessionNotFoundError:
            pass
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE, **_cookie_kwargs())
    return response


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Signed-in browsers, this one first, then the most recently active."""
    current_id = request.state.session_id
    sessions = await active_sessions(db, current_user.id)
    sessions.sort(key=lambda s: s.id != current_id)  # stable: keeps last_seen_at order
    return [
        SessionOut(
            id=s.id,
            device=s.device,
            device_kind=s.device_kind,
            ip_masked=s.ip_masked,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            current=s.id == current_id,
        )
        for s in sessions
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out_session(
    session_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if session_id == request.state.session_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Use /api/auth/logout for this device")
    try:
        await revoke_session(db, current_user.id, session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


@router.post("/sessions/revoke-others")
async def sign_out_other_sessions(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return {"revoked": await revoke_other_sessions(db, current_user.id, request.state.session_id)}


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Delete the account, its storage config and slip fingerprints; signs this browser out."""
    await auth_service.delete_account(current_user)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE, **_cookie_kwargs())
    return response

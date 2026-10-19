from urllib.parse import urlencode

from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse

from core.config import settings
from core.cookies import _cookie_kwargs, set_auth_cookie
from core.dependencies import get_auth_service
from core.oauth import oauth
from core.security import ACCESS_TOKEN_COOKIE, get_current_user
from models.users import User
from services.auth.auth_service import (
    GOOGLE_LOGIN_NEEDS_REFRESH_TOKEN,
    AuthService,
    GoogleLoginError,
    NotionLoginError,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _frontend_redirect(**params: str) -> RedirectResponse:
    url = f"{settings.frontend_url.rstrip('/')}/auth/callback?{urlencode(params)}"
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


# Set while a consent re-prompt is in flight, so a missing refresh token can't loop.
_GOOGLE_RECONSENT_FLAG = "google_reconsent_attempted"


@router.get("/google/login")
async def login(request: Request, reconsent: bool = False):
    # Google only returns a refresh token on the consent screen.
    params = {"prompt": "consent"} if reconsent else {}
    return await oauth.google.authorize_redirect(request, settings.redirect_uri, **params)


@router.get("/google/callback")
async def callback(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    reconsent_attempted = request.session.pop(_GOOGLE_RECONSENT_FLAG, False)
    try:
        token = await oauth.google.authorize_access_token(request)
        result = await auth_service.login_with_google(token)
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


@router.get("/notion/login")
async def notion_login(request: Request):
    return await oauth.notion.authorize_redirect(request, settings.notion_redirect_uri)


@router.get("/notion/callback")
async def notion_callback(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    try:
        token = await oauth.notion.authorize_access_token(request)
        _, jwt_token = await auth_service.login_with_notion(token)
    except OAuthError as exc:
        return _frontend_redirect(error=exc.error or "oauth_error")
    except NotionLoginError:
        return _frontend_redirect(error="invalid_notion_owner")

    response = _frontend_redirect(status="success")
    set_auth_cookie(response, jwt_token)
    return response


@router.post("/logout")
async def logout():
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE, **_cookie_kwargs())
    return response


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)):
    return current_user

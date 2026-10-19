from urllib.parse import urlencode

from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from core.config import settings
from core.cookies import _cookie_kwargs, set_auth_cookie
from core.dependencies import get_auth_service
from core.oauth import oauth
from core.security import (
    ACCESS_TOKEN_COOKIE,
    ACCESS_TOKEN_MAX_AGE,
    get_current_user,
)
from models.users import User
from services.auth.auth_service import AuthService, NotionLoginError

router = APIRouter(prefix="/auth", tags=["auth"])


def _frontend_redirect(**params: str) -> RedirectResponse:
    url = f"{settings.frontend_url.rstrip('/')}/auth/callback?{urlencode(params)}"
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.get("/google/login")
async def login(request: Request):
    redirect_uri = settings.redirect_uri
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback")
async def callback(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")
    if not user_info:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google user info missing",
        )

    user, jwt_token = await auth_service.login_with_google(user_info)

    response = JSONResponse({
        "message": "Login successful",
        "user": {"id": user.id, "email": user.email, "name": user.name},
    })
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=jwt_token,
        max_age=ACCESS_TOKEN_MAX_AGE,
        **_cookie_kwargs(),
    )
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

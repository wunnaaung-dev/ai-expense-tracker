from starlette.responses import Response

from core.config import settings
from core.security import ACCESS_TOKEN_COOKIE, ACCESS_TOKEN_MAX_AGE

COOKIE_SAMESITE = "lax"

def _cookie_kwargs() -> dict:
    return {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": COOKIE_SAMESITE,
    }

def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=token,
        max_age=ACCESS_TOKEN_MAX_AGE,
        **_cookie_kwargs(),
    )

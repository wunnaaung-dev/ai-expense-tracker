from core.config import settings

COOKIE_SAMESITE = "lax"

def _cookie_kwargs() -> dict:
    return {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": COOKIE_SAMESITE,
    }